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

_SESSION_RESUME_PROMPT = (
    "Continue exactly where you left off. Your previous run in this session was "
    "interrupted before it finished. Review what you have already done here, "
    "complete the remaining work, and produce your final output in the same "
    "format you were originally asked for — do not restart work you have "
    "already completed."
)

ISSUES_FOUND_MARKER = "ISSUES_FOUND"

# QA-cycle stages, in execution order. A resume cursor names the stage the cycle
# died at so the next run continues that exact Claude session instead of
# restarting the whole cycle.
STAGE_ACTION = "action"
STAGE_INITIAL_QA = "initial_qa"
STAGE_CORRECTION = "correction"
STAGE_RE_REVIEW = "re_review"
_STAGE_ORDER = {
    STAGE_ACTION: 0,
    STAGE_INITIAL_QA: 1,
    STAGE_CORRECTION: 2,
    STAGE_RE_REVIEW: 3,
}


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
        result = await claude.run(
            agent=agent_config, prompt=prompt, working_dir=workspace,
            session_id=session_id,
        )

    if not result.text.strip():
        raise AgentRunError(
            agent_name=agent_name,
            message=error_message,
            # Carry the session so a later resume continues it rather than
            # restarting the agent from scratch.
            session_id=result.session_id,
        )

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
    resume_session_id: str | None = None,
) -> tuple[str, float]:
    """Run QA agent and return (report_text, cost). Raises on empty output.

    When ``resume_session_id`` is set the QA agent's prior session (its full
    prompt and partial review) already lives in that session's history, so a
    short "continue where you left off" nudge is sent with ``--resume`` instead
    of re-rendering and re-billing the whole review.
    """
    if resume_session_id:
        qa_prompt = _SESSION_RESUME_PROMPT
    else:
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
        session_id=resume_session_id,
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
    on_progress: Callable[[str, str | None, int], None] | None = None,
    resume_stage: str | None = None,
    resume_round: int = 0,
    skip_to_correction: bool = False,
    mcp_config: Path | None = None,
    content_markers: list[str] | None = None,
    skip_action_output_in_qa: bool = False,
    figma_mcp_enabled: bool = False,
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
        on_progress: Optional callback ``(stage, session_id, round)`` invoked as
            each stage begins and, in the failure path, with the failed agent's
            session id. The caller persists this cursor so the next
            ``agentic-dev resume`` continues the exact session/stage that died.
            ``stage`` is one of ``"action"``, ``"initial_qa"``, ``"correction"``,
            ``"re_review"``.
        resume_stage: When set, re-enter the cycle at this stage instead of
            running from the top — loading already-saved outputs from the
            doc_store and resuming ``session_id`` for that stage.
        resume_round: The correction/re-review round to resume at (for
            ``resume_stage`` in ``{"correction", "re_review"}``).
        skip_to_correction: Deprecated alias for ``resume_stage="correction"``;
            skip the action agent and initial QA review, loading their outputs
            from the doc_store instead.
        skip_action_output_in_qa: When True, the action agent's output is NOT
            injected into the QA prompt as ``{qa_output_key: output_text}``.
            Only safe for code-review QA where the reviewer re-reads the
            filesystem (e.g. backend_qa, frontend_qa). Do NOT enable this for
            QA agents whose prompt template references the action output key
            directly (e.g. ``integration_qa`` renders ``{{ integration_guide
            }}``) — rendering will fail with ``StrictUndefined``.
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
    qa_report_name = f"qa/{output_doc_name}"

    action_config = to_run_config(
        action_agent, mcp_config=mcp_config, figma_mcp_enabled=figma_mcp_enabled,
    )
    qa_config = to_run_config(qa_agent, figma_mcp_enabled=figma_mcp_enabled)

    # ``skip_to_correction`` is the legacy spelling of "resume at the correction
    # stage" (no session continuity). Map it onto the stage cursor.
    if skip_to_correction and resume_stage is None:
        resume_stage = STAGE_CORRECTION
        resume_round = resume_round or 1
    entry = _STAGE_ORDER.get(resume_stage, 0) if resume_stage else 0

    def _progress(stage: str, sid: str | None, rnd: int) -> None:
        if on_progress is not None:
            on_progress(stage, sid, rnd)

    async def _stage(stage, rnd, sid_in, runner):
        """Run one sub-step, reporting its session via on_progress and, on
        failure, the failed session before the error propagates."""
        _progress(stage, sid_in, rnd)
        try:
            return await runner()
        except AgentRunError as exc:
            _progress(stage, exc.session_id, rnd)
            raise

    action_cost = 0.0
    initial_qa_cost = 0.0
    action_session_id: str | None = None
    ran_initial_qa = False

    # ---- ACTION ----
    if entry <= _STAGE_ORDER[STAGE_ACTION]:
        # When resuming a prior session, the original prompt and all prior work
        # already live in that session's history, so send a short "continue
        # where you left off" nudge instead of re-piping the full prompt (which
        # would re-bill the prior context).
        if session_id:
            action_prompt = _SESSION_RESUME_PROMPT
        else:
            action_prompt = prompt_renderer.render_agent_prompt(
                template_name=action_agent.prompt_template,
                input_documents=input_docs,
                constraints=action_agent.constraints,
                extra_context=extra_context,
            )
        action_result = await _stage(
            STAGE_ACTION, 0, session_id,
            lambda: _run_with_empty_retry(
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
            ),
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

        doc_store.write(output_doc_name, action_output_text)
        _check_budget(
            action_agent.name, action_cost,
            action_agent.claude.max_budget_usd, sprint,
        )
    else:
        # Resuming past the action stage: its output is already on disk. For a
        # correction/re-review resume, the session being resumed is the
        # action(correction) session — carry it so later corrections continue it.
        action_output_text = doc_store.read(output_doc_name)
        if entry >= _STAGE_ORDER[STAGE_CORRECTION]:
            action_session_id = session_id

    # ---- INITIAL QA ----
    if entry <= _STAGE_ORDER[STAGE_INITIAL_QA]:
        if on_substep is not None:
            on_substep("qa")
        qa_resume = session_id if resume_stage == STAGE_INITIAL_QA else None
        initial_qa_report, initial_qa_cost = await _stage(
            STAGE_INITIAL_QA, 0, qa_resume,
            lambda: _run_qa_review(
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
                resume_session_id=qa_resume,
            ),
        )
        doc_store.write(qa_report_name, initial_qa_report)
        ran_initial_qa = True
    else:
        initial_qa_report = doc_store.read(qa_report_name)

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

    start_round = (
        resume_round
        if entry >= _STAGE_ORDER[STAGE_CORRECTION] and resume_round
        else 1
    )

    for round_num in range(start_round, max_corrections + 1):
        if ISSUES_FOUND_MARKER not in latest_qa_report:
            break

        resuming_this = (
            entry >= _STAGE_ORDER[STAGE_CORRECTION] and round_num == resume_round
        )
        # When resuming at re-review, the correction for this round already ran
        # and its output is saved — skip straight to the re-review.
        resume_at_re_review = resuming_this and resume_stage == STAGE_RE_REVIEW

        # Preserve the initial QA report before overwrites (only when we
        # produced it fresh this run).
        if round_num == 1 and ran_initial_qa:
            doc_store.write(
                f"qa/{output_doc_name}_initial", initial_qa_report
            )

        if resume_at_re_review:
            latest_output = doc_store.read(output_doc_name)
            correction_cost = 0.0
        else:
            if on_substep is not None:
                on_substep("correction")

            correction_session_id: str | None = None
            if resuming_this and resume_stage == STAGE_CORRECTION and session_id:
                # Resume the in-flight correction session where it stopped.
                correction_prompt = _SESSION_RESUME_PROMPT
                correction_session_id = session_id
            elif action_session_id:
                # Continue the action session to avoid re-embedding the full
                # previous output in the prompt.
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
            correction_result = await _stage(
                STAGE_CORRECTION, round_num, correction_session_id,
                lambda cp=correction_prompt, cs=correction_session_id: (
                    _run_with_empty_retry(
                        claude=claude,
                        agent_config=action_config,
                        prompt=cp,
                        workspace=workspace,
                        agent_name=action_agent.name,
                        error_message="Agent returned empty output after correction",
                        sprint=sprint,
                        max_empty_retries=max_empty_retries,
                        empty_retry_delay=empty_retry_delay,
                        session_id=cs,
                    )
                ),
            )

            latest_output = correction_result.text
            doc_store.write(output_doc_name, latest_output)
            # Update session ID for potential subsequent correction rounds
            if correction_result.session_id:
                action_session_id = correction_result.session_id
            correction_cost = correction_result.cost_usd

            emit(_event_log, QACycleCorrectionEvent(
                action_agent=action_agent.name,
                correction_cost=correction_cost,
                round_number=round_num,
                sprint=sprint,
                message=(
                    f"Correction round {round_num} for {action_agent.name} "
                    f"(${correction_cost:.4f})"
                ),
            ))

        # Re-review: QA agent evaluates corrected output
        rr_resume = session_id if resume_at_re_review else None
        re_review_report, re_review_cost = await _stage(
            STAGE_RE_REVIEW, round_num, rr_resume,
            lambda rr=rr_resume: _run_qa_review(
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
                resume_session_id=rr,
            ),
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
            f"qa/{output_doc_name}_round_{round_num}", re_review_report
        )
        doc_store.write(qa_report_name, re_review_report)

        corrections.append(CorrectionRound(
            correction_cost=correction_cost,
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
