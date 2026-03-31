"""Tests for the QA cycle orchestrator."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_dev.agents.base import AgentDefinition, ClaudeConfig
from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import AgentRunError
from agentic_dev.orchestrator.qa_cycle import QACycleResult, run_qa_cycle
from agentic_dev.prompts.renderer import PromptRenderer


def _make_agent(name: str, template: str = "tpl.md.j2") -> AgentDefinition:
    """Helper to build a minimal AgentDefinition for testing."""
    return AgentDefinition(
        name=name,
        description=f"{name} agent",
        team="test",
        claude=ClaudeConfig(
            model="sonnet",
            permission_mode="plan",
            allowed_tools=["Read"],
            max_budget_usd=1.0,
        ),
        prompt_template=template,
        input_documents=["input.md"],
    )


def _make_claude_result(text: str, cost: float = 0.10) -> ClaudeResult:
    return ClaudeResult(
        text=text,
        session_id="sess-123",
        cost_usd=cost,
        exit_code=0,
    )


@pytest.fixture
def action_agent() -> AgentDefinition:
    return _make_agent("action_agent", "action.md.j2")


@pytest.fixture
def qa_agent() -> AgentDefinition:
    return _make_agent("qa_agent", "qa.md.j2")


@pytest.fixture
def claude() -> ClaudeRunner:
    runner = MagicMock(spec=ClaudeRunner)
    runner.run = AsyncMock()
    return runner


@pytest.fixture
def doc_store(tmp_path: Path) -> DocumentStore:
    store = MagicMock(spec=DocumentStore)
    return store


@pytest.fixture
def prompt_renderer() -> PromptRenderer:
    renderer = MagicMock(spec=PromptRenderer)
    renderer.render_agent_prompt = MagicMock(return_value="rendered prompt")
    return renderer


@pytest.mark.asyncio
async def test_successful_cycle_no_issues(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When QA approves, no correction run occurs."""
    claude.run.side_effect = [
        _make_claude_result("action output", cost=0.15),
        _make_claude_result("APPROVED: looks good", cost=0.10),
    ]

    result = await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={"input.md": "requirements"},
        output_doc_name="design.md",
        workspace=Path("/tmp/workspace"),
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
    )

    assert result.output == "action output"
    assert result.corrected is False
    assert result.action_cost == 0.15
    assert result.qa_cost == 0.10
    assert result.correction_cost == 0.0
    assert claude.run.call_count == 2


@pytest.mark.asyncio
async def test_cycle_with_issues_triggers_correction(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When QA finds issues, a correction run is triggered."""
    claude.run.side_effect = [
        _make_claude_result("initial output", cost=0.15),
        _make_claude_result("ISSUES_FOUND: missing error handling", cost=0.10),
        _make_claude_result("corrected output", cost=0.20),
    ]

    result = await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={"input.md": "requirements"},
        output_doc_name="design.md",
        workspace=Path("/tmp/workspace"),
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
    )

    assert result.output == "corrected output"
    assert result.corrected is True
    assert result.action_cost == 0.15
    assert result.qa_cost == 0.10
    assert result.correction_cost == 0.20
    assert claude.run.call_count == 3


@pytest.mark.asyncio
async def test_costs_tracked_correctly(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """All costs are captured in the result."""
    claude.run.side_effect = [
        _make_claude_result("output", cost=0.50),
        _make_claude_result("APPROVED", cost=0.25),
    ]

    result = await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={},
        output_doc_name="out.md",
        workspace=Path("/tmp/ws"),
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
    )

    total = result.action_cost + result.qa_cost + result.correction_cost
    assert total == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_documents_saved_to_store(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """Output and QA report are written to the document store."""
    claude.run.side_effect = [
        _make_claude_result("the output", cost=0.10),
        _make_claude_result("APPROVED", cost=0.05),
    ]

    await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={"req.md": "reqs"},
        output_doc_name="result.md",
        workspace=Path("/tmp/ws"),
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
    )

    # Output document written once (no correction)
    doc_store.write.assert_any_call("result.md", "the output")
    # QA report saved under qa_reports/
    doc_store.write.assert_any_call("qa_reports/result.md", "APPROVED")


@pytest.mark.asyncio
async def test_correction_overwrites_output_document(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When correction runs, the output document is overwritten."""
    claude.run.side_effect = [
        _make_claude_result("v1", cost=0.10),
        _make_claude_result("ISSUES_FOUND: bad", cost=0.05),
        _make_claude_result("v2", cost=0.10),
    ]

    await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={},
        output_doc_name="doc.md",
        workspace=Path("/tmp/ws"),
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
    )

    # The document is written twice: initial and corrected
    write_calls = [
        call for call in doc_store.write.call_args_list if call[0][0] == "doc.md"
    ]
    assert len(write_calls) == 2
    assert write_calls[0][0][1] == "v1"
    assert write_calls[1][0][1] == "v2"


@pytest.mark.asyncio
async def test_correction_prompt_uses_correction_mode(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """The correction run renders the prompt with correction_mode=True."""
    claude.run.side_effect = [
        _make_claude_result("v1", cost=0.10),
        _make_claude_result("ISSUES_FOUND: fix it", cost=0.05),
        _make_claude_result("v2", cost=0.10),
    ]

    await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={"req.md": "reqs"},
        output_doc_name="doc.md",
        workspace=Path("/tmp/ws"),
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
    )

    # Third call to render_agent_prompt should have correction_mode=True
    correction_call = prompt_renderer.render_agent_prompt.call_args_list[2]
    assert correction_call.kwargs.get("correction_mode") is True
    assert correction_call.kwargs.get("previous_output") == "v1"
    assert "ISSUES_FOUND" in correction_call.kwargs.get("qa_feedback", "")


@pytest.mark.asyncio
async def test_empty_action_output_raises_error(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """An empty result from the action agent should raise AgentRunError."""
    claude.run.side_effect = [
        _make_claude_result("", cost=0.10),
    ]

    with pytest.raises(AgentRunError, match="empty output"):
        await run_qa_cycle(
            claude=claude,
            action_agent=action_agent,
            qa_agent=qa_agent,
            input_docs={"input.md": "requirements"},
            output_doc_name="out.md",
            workspace=Path("/tmp/ws"),
            doc_store=doc_store,
            prompt_renderer=prompt_renderer,
        )


@pytest.mark.asyncio
async def test_empty_correction_output_raises_error(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """An empty result from the correction run should raise AgentRunError."""
    claude.run.side_effect = [
        _make_claude_result("initial output", cost=0.15),
        _make_claude_result("ISSUES_FOUND: fix it", cost=0.10),
        _make_claude_result("   ", cost=0.20),
    ]

    with pytest.raises(AgentRunError, match="empty output after correction"):
        await run_qa_cycle(
            claude=claude,
            action_agent=action_agent,
            qa_agent=qa_agent,
            input_docs={"input.md": "requirements"},
            output_doc_name="out.md",
            workspace=Path("/tmp/ws"),
            doc_store=doc_store,
            prompt_renderer=prompt_renderer,
        )


@pytest.mark.asyncio
async def test_qa_output_key_overrides_output_doc_name_for_qa_input(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When qa_output_key is provided, the QA agent receives the action output
    under that key instead of the output_doc_name."""
    claude.run.side_effect = [
        _make_claude_result("action output", cost=0.15),
        _make_claude_result("APPROVED", cost=0.10),
    ]

    await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={"api_contract": "contract"},
        output_doc_name="sprint_1_integration",
        qa_output_key="integration_guide",
        workspace=Path("/tmp/ws"),
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
    )

    # The QA prompt should be rendered with 'integration_guide' as the key
    qa_render_call = prompt_renderer.render_agent_prompt.call_args_list[1]
    qa_input_docs = qa_render_call.kwargs.get(
        "input_documents",
        qa_render_call.args[1] if len(qa_render_call.args) > 1 else None,
    )
    assert "integration_guide" in qa_input_docs
    assert qa_input_docs["integration_guide"] == "action output"
    # The dynamic output_doc_name should NOT be a key
    assert "sprint_1_integration" not in qa_input_docs


@pytest.mark.asyncio
async def test_qa_output_key_defaults_to_output_doc_name(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When qa_output_key is not provided, behavior is unchanged."""
    claude.run.side_effect = [
        _make_claude_result("action output", cost=0.15),
        _make_claude_result("APPROVED", cost=0.10),
    ]

    await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={"req": "requirements"},
        output_doc_name="result.md",
        workspace=Path("/tmp/ws"),
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
    )

    qa_render_call = prompt_renderer.render_agent_prompt.call_args_list[1]
    qa_input_docs = qa_render_call.kwargs.get(
        "input_documents",
        qa_render_call.args[1] if len(qa_render_call.args) > 1 else None,
    )
    assert "result.md" in qa_input_docs


@pytest.mark.asyncio
async def test_empty_qa_output_raises_error(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """An empty result from the QA agent should raise AgentRunError."""
    claude.run.side_effect = [
        _make_claude_result("valid action output", cost=0.15),
        _make_claude_result("", cost=0.10),
    ]

    with pytest.raises(AgentRunError, match="QA agent returned empty output"):
        await run_qa_cycle(
            claude=claude,
            action_agent=action_agent,
            qa_agent=qa_agent,
            input_docs={"input.md": "requirements"},
            output_doc_name="out.md",
            workspace=Path("/tmp/ws"),
            doc_store=doc_store,
            prompt_renderer=prompt_renderer,
        )


@pytest.mark.asyncio
async def test_whitespace_only_qa_output_raises_error(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """Whitespace-only QA output should also raise AgentRunError."""
    claude.run.side_effect = [
        _make_claude_result("valid action output", cost=0.15),
        _make_claude_result("   \n  ", cost=0.10),
    ]

    with pytest.raises(AgentRunError, match="QA agent returned empty output"):
        await run_qa_cycle(
            claude=claude,
            action_agent=action_agent,
            qa_agent=qa_agent,
            input_docs={"input.md": "requirements"},
            output_doc_name="out.md",
            workspace=Path("/tmp/ws"),
            doc_store=doc_store,
            prompt_renderer=prompt_renderer,
        )
