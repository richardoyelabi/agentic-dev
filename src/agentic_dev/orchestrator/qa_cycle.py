"""Reusable QA cycle: action agent -> QA agent -> optional correction."""

from dataclasses import dataclass
from pathlib import Path

from agentic_dev.agents.base import AgentDefinition
from agentic_dev.claude.runner import ClaudeRunner
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import AgentRunError
from agentic_dev.orchestrator.agent_bridge import to_run_config
from agentic_dev.prompts.renderer import PromptRenderer

ISSUES_FOUND_MARKER = "ISSUES_FOUND"


@dataclass(frozen=True)
class QACycleResult:
    """Outcome of a single QA cycle."""

    output: str
    qa_report: str
    corrected: bool
    action_cost: float
    qa_cost: float
    correction_cost: float = 0.0


async def run_qa_cycle(
    claude: ClaudeRunner,
    action_agent: AgentDefinition,
    qa_agent: AgentDefinition,
    input_docs: dict[str, str],
    output_doc_name: str,
    workspace: Path,
    doc_store: DocumentStore,
    prompt_renderer: PromptRenderer,
) -> QACycleResult:
    """Execute one action -> QA -> optional correction cycle.

    The QA agent reviews independently. If it signals issues (by including
    ``ISSUES_FOUND`` in its output), the action agent runs once more with
    the original inputs plus the QA feedback. No retry loops.
    """
    # 1. Render and run the action agent
    action_prompt = prompt_renderer.render_agent_prompt(
        template_name=action_agent.prompt_template,
        input_documents=input_docs,
        constraints=action_agent.constraints,
    )
    action_config = to_run_config(action_agent)
    action_result = await claude.run(
        agent=action_config,
        prompt=action_prompt,
        working_dir=workspace,
    )

    if not action_result.text.strip():
        raise AgentRunError(
            agent_name=action_agent.name,
            message="Agent returned empty output",
        )

    # 2. Save the action output
    doc_store.write(output_doc_name, action_result.text)

    # 3. Render and run the QA agent
    qa_input_docs = {**input_docs, output_doc_name: action_result.text}
    qa_prompt = prompt_renderer.render_agent_prompt(
        template_name=qa_agent.prompt_template,
        input_documents=qa_input_docs,
        constraints=qa_agent.constraints,
    )
    qa_config = to_run_config(qa_agent)
    qa_result = await claude.run(
        agent=qa_config,
        prompt=qa_prompt,
        working_dir=workspace,
    )

    # 4. Save the QA report
    qa_report_name = f"qa_reports/{output_doc_name}"
    doc_store.write(qa_report_name, qa_result.text)

    # 5. Check for issues and optionally correct
    correction_cost = 0.0
    corrected = False
    final_output = action_result.text

    if ISSUES_FOUND_MARKER in qa_result.text:
        correction_prompt = prompt_renderer.render_agent_prompt(
            template_name=action_agent.prompt_template,
            input_documents=input_docs,
            constraints=action_agent.constraints,
            correction_mode=True,
            previous_output=action_result.text,
            qa_feedback=qa_result.text,
        )
        correction_result = await claude.run(
            agent=action_config,
            prompt=correction_prompt,
            working_dir=workspace,
        )
        correction_cost = correction_result.cost_usd
        corrected = True
        final_output = correction_result.text

        if not final_output.strip():
            raise AgentRunError(
                agent_name=action_agent.name,
                message="Agent returned empty output after correction",
            )

        doc_store.write(output_doc_name, final_output)

    return QACycleResult(
        output=final_output,
        qa_report=qa_result.text,
        corrected=corrected,
        action_cost=action_result.cost_usd,
        qa_cost=qa_result.cost_usd,
        correction_cost=correction_cost,
    )
