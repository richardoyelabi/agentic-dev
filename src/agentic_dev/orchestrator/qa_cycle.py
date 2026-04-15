"""Reusable QA cycle: action agent -> QA agent -> correction loop with re-review."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from agentic_dev.agents.base import AgentDefinition
from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import AgentRunError
from agentic_dev.logging import get_event_logger, emit
from agentic_dev.logging.context import get_run_context
from agentic_dev.logging.events import (
    AgentEmptyRetryEvent,
    BudgetWarningEvent,
    ContentMarkerRecoveryEvent,
    QACycleStartEvent,
    QACycleVerdictEvent,
    QACycleCorrectionEvent,
    QACycleReReviewEvent,
    QACycleCompleteEvent,
)
from agentic_dev.orchestrator.agent_bridge import AgentRunConfig, to_run_config
from agentic_dev.prompts.renderer import PromptRenderer

_event_log = get_event_logger("qa_cycle")

_SESSION_CORRECTION_PROMPT = (
    "A quality assurance reviewer has found issues with your output:\n\n"
    "{qa_feedback}\n\n"
    "Please address all feedback and produce a corrected version of your "
    "output. Maintain the same output format."
)

ISSUES_FOUND_MARKER = "ISSUES_FOUND"


async def _run_with_empty_retry(
    claude: ClaudeRunner,
    agent_config: AgentRunConfig,
    prompt: str,
    workspace: Path,
    agent_name: str,
    error_message: str,
    sprint: int | None = None,
    max_empty_retries: int = 1,
    empty_retry_delay: float = 5.0,
    session_id: str | None = None,
) -> ClaudeResult:
    """Run an agent and retry once if it returns empty output.

    Raises AgentRunError when all attempts produce empty output.
    """
    result = await claude.run(
        agent=agent_config, prompt=prompt, working_dir=workspace,
        session_id=session_id,
    )

    for attempt in range(1, max_empty_retries + 1):
        if result.text.strip():
            return result

        emit(_event_log, AgentEmptyRetryEvent(
            agent_name=agent_name,
            attempt=attempt,
            max_retries=max_empty_retries,
            wait_seconds=empty_retry_delay,
            sprint=sprint,
            level="WARNING",
            message=(
                f"Agent '{agent_name}' returned empty output — "
                f"retrying (attempt {attempt}/{max_empty_retries})"
            ),
        ))
        await asyncio.sleep(empty_retry_delay)
        result = await claude.run(agent=agent_config, prompt=prompt, working_dir=workspace)

    if not result.text.strip():
        raise AgentRunError(agent_name=agent_name, message=error_message)

    return result


@dataclass(frozen=True)
class CorrectionRound:
    """One correction + re-review pass."""

    correction_cost: float
    re_review_cost: float
    qa_report: str


@dataclass(frozen=True)
class QACycleResult:
    """Outcome of a single QA cycle."""

    output: str
    initial_qa_report: str
    final_qa_report: str
    corrections: list[CorrectionRound] = field(default_factory=list)
    action_cost: float = 0.0
    initial_qa_cost: float = 0.0
    session_id: str | None = None

    @property
    def corrected(self) -> bool:
        return len(self.corrections) > 0

    @property
    def correction_cost(self) -> float:
        return sum(r.correction_cost for r in self.corrections)

    @property
    def re_review_cost(self) -> float:
        return sum(r.re_review_cost for r in self.corrections)

    @property
    def total_cost(self) -> float:
        return (
            self.action_cost
            + self.initial_qa_cost
            + self.correction_cost
            + self.re_review_cost
        )


def _check_budget(
    agent_name: str,
    cost_usd: float,
    max_budget_usd: float,
    sprint: int | None = None,
) -> None:
    """Emit a warning if cost exceeds the agent's budget."""
    if cost_usd > max_budget_usd:
        emit(_event_log, BudgetWarningEvent(
            agent_name=agent_name,
            cost_usd=cost_usd,
            max_budget_usd=max_budget_usd,
            sprint=sprint,
            level="WARNING",
            message=(
                f"Agent '{agent_name}' exceeded budget: "
                f"${cost_usd:.4f} > ${max_budget_usd:.2f}"
            ),
        ))


async def _run_qa_review(
    claude: ClaudeRunner,
    qa_agent: AgentDefinition,
    qa_config: AgentRunConfig,
    input_docs: dict[str, str],
    qa_key: str,
    output_text: str,
    prompt_renderer: PromptRenderer,
    workspace: Path,
    extra_context: dict[str, str] | None = None,
    sprint: int | None = None,
    max_empty_retries: int = 1,
    empty_retry_delay: float = 5.0,
    skip_action_output: bool = False,
) -> tuple[str, float]:
    """Run QA agent and return (report_text, cost). Raises on empty output."""
    if skip_action_output:
        qa_input_docs = dict(input_docs)
    else:
        qa_input_docs = {**input_docs, qa_key: output_text}
    qa_prompt = prompt_renderer.render_agent_prompt(
        template_name=qa_agent.prompt_template,
        input_documents=qa_input_docs,
        constraints=qa_agent.constraints,
        extra_context=extra_context,
    )
    qa_result = await _run_with_empty_retry(
        claude=claude,
        agent_config=qa_config,
        prompt=qa_prompt,
        workspace=workspace,
        agent_name=qa_agent.name,
        error_message="QA agent returned empty output",
        sprint=sprint,
        max_empty_retries=max_empty_retries,
        empty_retry_delay=empty_retry_delay,
    )

    return qa_result.text, qa_result.cost_usd


async def run_qa_cycle(
    claude: ClaudeRunner,
    action_agent: AgentDefinition,
    qa_agent: AgentDefinition,
    input_docs: dict[str, str],
    output_doc_name: str,
    workspace: Path,
    doc_store: DocumentStore,
    prompt_renderer: PromptRenderer,
    qa_output_key: str | None = None,
    extra_context: dict[str, str] | None = None,
    max_corrections: int = 1,
    max_empty_retries: int = 1,
    empty_retry_delay: float = 5.0,
    session_id: str | None = None,
    on_substep: Callable[[str], None] | None = None,
    skip_to_correction: bool = False,
    mcp_config: Path | None = None,
    content_markers: list[str] | None = None,
    skip_action_output_in_qa: bool = False,
) -> QACycleResult:
    """Execute one action -> QA -> correction loop cycle.

    After each correction, QA re-reviews the corrected output. The loop exits
    when QA approves or ``max_corrections`` rounds are exhausted. The user
    always sees QA feedback on the final version of the output.

    Args:
        max_corrections: Maximum number of correction rounds. Defaults to 1.
            Set to 0 to make QA informational only (no corrections).
        on_substep: Optional callback invoked at sub-step boundaries with
            ``"qa"`` (before QA runs) or ``"correction"`` (before correction).
        skip_to_correction: When True, skip the action agent and initial QA
            review, loading their outputs from the doc_store instead. Used
            to resume mid-QA-cycle after a crash.
    """
    ctx = get_run_context()
    sprint = ctx.sprint_number if ctx else None

    emit(_event_log, QACycleStartEvent(
        action_agent=action_agent.name,
        qa_agent=qa_agent.name,
        output_doc_name=output_doc_name,
        sprint=sprint,
        message=f"QA cycle: {action_agent.name} -> {qa_agent.name} for '{output_doc_name}'",
    ))

    qa_key = qa_output_key or output_doc_name
    qa_report_name = f"qa_reports/{output_doc_name}"

    action_config = to_run_config(action_agent, mcp_config=mcp_config)
    qa_config = to_run_config(qa_agent)

    if skip_to_correction:
        # Resume: load prior action output and QA report from doc_store
        action_output_text = doc_store.read(output_doc_name)
        initial_qa_report = doc_store.read(qa_report_name)
        action_cost = 0.0
        initial_qa_cost = 0.0
        action_session_id: str | None = None
    else:
        # 1. Render and run the action agent
        action_prompt = prompt_renderer.render_agent_prompt(
            template_name=action_agent.prompt_template,
            input_documents=input_docs,
            constraints=action_agent.constraints,
            extra_context=extra_context,
        )
        action_result = await _run_with_empty_retry(
            claude=claude,
            agent_config=action_config,
            prompt=action_prompt,
            workspace=workspace,
            agent_name=action_agent.name,
            error_message="Agent returned empty output",
            sprint=sprint,
            max_empty_retries=max_empty_retries,
            empty_retry_delay=empty_retry_delay,
            session_id=session_id,
        )

        action_output_text = action_result.text
        action_cost = action_result.cost_usd
        action_session_id = action_result.session_id

        # Content-marker recovery: if the result doesn't contain expected
        # markers, the real document may be in an earlier session message.
        if (
            content_markers
            and action_session_id
            and not all(m in action_output_text for m in content_markers)
        ):
            recovered = ClaudeRunner._recover_longest_from_session(
                action_session_id, workspace,
            )
            if recovered.strip() and all(
                m in recovered for m in content_markers
            ):
                emit(_event_log, ContentMarkerRecoveryEvent(
                    action_agent=action_agent.name,
                    session_id=action_session_id,
                    original_length=len(action_output_text),
                    recovered_length=len(recovered),
                    sprint=sprint,
                    message=(
                        f"Content-marker recovery for {action_agent.name}: "
                        f"replaced {len(action_output_text)} chars with "
                        f"{len(recovered)} chars from session {action_session_id}"
                    ),
                ))
                action_output_text = recovered

        # 2. Save the action output and check budget
        doc_store.write(output_doc_name, action_output_text)
        _check_budget(
            action_agent.name, action_cost,
            action_agent.claude.max_budget_usd, sprint,
        )

        if on_substep is not None:
            on_substep("qa")

        # 3. Run the initial QA review
        initial_qa_report, initial_qa_cost = await _run_qa_review(
            claude=claude,
            qa_agent=qa_agent,
            qa_config=qa_config,
            input_docs=input_docs,
            qa_key=qa_key,
            output_text=action_output_text,
            prompt_renderer=prompt_renderer,
            workspace=workspace,
            extra_context=extra_context,
            sprint=sprint,
            max_empty_retries=max_empty_retries,
            empty_retry_delay=empty_retry_delay,
            skip_action_output=skip_action_output_in_qa,
        )

        # 4. Save the initial QA report
        doc_store.write(qa_report_name, initial_qa_report)

    issues_found = ISSUES_FOUND_MARKER in initial_qa_report
    emit(_event_log, QACycleVerdictEvent(
        action_agent=action_agent.name,
        qa_agent=qa_agent.name,
        issues_found=issues_found,
        sprint=sprint,
        message=f"QA verdict: {'issues found' if issues_found else 'approved'} ({qa_agent.name})",
    ))

    # 5. Correction loop
    corrections: list[CorrectionRound] = []
    latest_output = action_output_text
    latest_qa_report = initial_qa_report

    for round_num in range(1, max_corrections + 1):
        if ISSUES_FOUND_MARKER not in latest_qa_report:
            break

        if on_substep is not None:
            on_substep("correction")

        # Preserve the initial QA report before overwrites
        if round_num == 1:
            doc_store.write(
                f"qa_reports/{output_doc_name}_initial", initial_qa_report
            )

        # Correction: prefer session continuation when we have a session ID
        # to avoid re-embedding the full previous output in the prompt.
        correction_session_id: str | None = None
        if action_session_id:
            correction_prompt = _SESSION_CORRECTION_PROMPT.format(
                qa_feedback=latest_qa_report,
            )
            correction_session_id = action_session_id
        else:
            correction_prompt = prompt_renderer.render_agent_prompt(
                template_name=action_agent.prompt_template,
                input_documents=input_docs,
                constraints=action_agent.constraints,
                correction_mode=True,
                previous_output=latest_output,
                qa_feedback=latest_qa_report,
                extra_context=extra_context,
            )
        correction_result = await _run_with_empty_retry(
            claude=claude,
            agent_config=action_config,
            prompt=correction_prompt,
            workspace=workspace,
            agent_name=action_agent.name,
            error_message="Agent returned empty output after correction",
            sprint=sprint,
            max_empty_retries=max_empty_retries,
            empty_retry_delay=empty_retry_delay,
            session_id=correction_session_id,
        )

        latest_output = correction_result.text
        doc_store.write(output_doc_name, latest_output)
        # Update session ID for potential subsequent correction rounds
        if correction_result.session_id:
            action_session_id = correction_result.session_id

        emit(_event_log, QACycleCorrectionEvent(
            action_agent=action_agent.name,
            correction_cost=correction_result.cost_usd,
            round_number=round_num,
            sprint=sprint,
            message=(
                f"Correction round {round_num} for {action_agent.name} "
                f"(${correction_result.cost_usd:.4f})"
            ),
        ))

        # Re-review: QA agent evaluates corrected output
        re_review_report, re_review_cost = await _run_qa_review(
            claude=claude,
            qa_agent=qa_agent,
            qa_config=qa_config,
            input_docs=input_docs,
            qa_key=qa_key,
            output_text=latest_output,
            prompt_renderer=prompt_renderer,
            workspace=workspace,
            extra_context=extra_context,
            sprint=sprint,
            max_empty_retries=max_empty_retries,
            empty_retry_delay=empty_retry_delay,
            skip_action_output=skip_action_output_in_qa,
        )

        re_review_issues = ISSUES_FOUND_MARKER in re_review_report
        emit(_event_log, QACycleReReviewEvent(
            action_agent=action_agent.name,
            qa_agent=qa_agent.name,
            round_number=round_num,
            issues_found=re_review_issues,
            re_review_cost=re_review_cost,
            sprint=sprint,
            message=(
                f"Re-review round {round_num}: "
                f"{'issues found' if re_review_issues else 'approved'} "
                f"({qa_agent.name})"
            ),
        ))

        doc_store.write(
            f"qa_reports/{output_doc_name}_round_{round_num}", re_review_report
        )
        doc_store.write(qa_report_name, re_review_report)

        corrections.append(CorrectionRound(
            correction_cost=correction_result.cost_usd,
            re_review_cost=re_review_cost,
            qa_report=re_review_report,
        ))
        latest_qa_report = re_review_report

    final_qa_report = latest_qa_report

    result = QACycleResult(
        output=latest_output,
        initial_qa_report=initial_qa_report,
        final_qa_report=final_qa_report,
        corrections=corrections,
        action_cost=action_cost,
        initial_qa_cost=initial_qa_cost,
        session_id=action_session_id,
    )

    emit(_event_log, QACycleCompleteEvent(
        action_agent=action_agent.name,
        qa_agent=qa_agent.name,
        corrected=result.corrected,
        correction_rounds=len(corrections),
        total_cost=result.total_cost,
        sprint=sprint,
        message=(
            f"QA cycle complete: {action_agent.name} "
            f"({len(corrections)} corrections, ${result.total_cost:.4f})"
        ),
    ))

    return result
