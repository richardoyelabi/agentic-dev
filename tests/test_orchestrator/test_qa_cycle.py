"""Tests for the QA cycle orchestrator."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_dev.agents.base import AgentDefinition, ClaudeConfig
from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import AgentRunError
from agentic_dev.orchestrator.qa_cycle import run_qa_cycle
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


# ---------------------------------------------------------------------------
# No-issues path (approved on first QA pass)
# ---------------------------------------------------------------------------


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
    assert result.initial_qa_cost == 0.10
    assert result.correction_cost == 0.0
    assert result.re_review_cost == 0.0
    assert result.corrections == []
    assert result.final_qa_report == result.initial_qa_report
    assert result.final_qa_report == "APPROVED: looks good"
    assert claude.run.call_count == 2


@pytest.mark.asyncio
async def test_final_qa_report_equals_initial_when_no_correction(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """Explicit check: final_qa_report mirrors initial_qa_report on clean pass."""
    claude.run.side_effect = [
        _make_claude_result("output", cost=0.10),
        _make_claude_result("All good, APPROVED", cost=0.05),
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

    assert result.final_qa_report is result.initial_qa_report


# ---------------------------------------------------------------------------
# Single correction round (default max_corrections=1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cycle_with_issues_triggers_correction_and_re_review(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When QA finds issues, a correction + re-review runs (4 claude calls)."""
    claude.run.side_effect = [
        _make_claude_result("initial output", cost=0.15),
        _make_claude_result("ISSUES_FOUND: missing error handling", cost=0.10),
        _make_claude_result("corrected output", cost=0.20),
        _make_claude_result("APPROVED after correction", cost=0.08),
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
    assert result.initial_qa_cost == 0.10
    assert len(result.corrections) == 1
    assert result.corrections[0].correction_cost == 0.20
    assert result.corrections[0].re_review_cost == 0.08
    assert result.corrections[0].qa_report == "APPROVED after correction"
    assert result.final_qa_report == "APPROVED after correction"
    assert result.initial_qa_report == "ISSUES_FOUND: missing error handling"
    assert claude.run.call_count == 4


# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_costs_tracked_correctly_no_correction(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """All costs are captured when no correction needed."""
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

    assert result.total_cost == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_costs_tracked_correctly_with_correction(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """All costs including correction and re-review are captured."""
    claude.run.side_effect = [
        _make_claude_result("v1", cost=0.50),
        _make_claude_result("ISSUES_FOUND: bad", cost=0.25),
        _make_claude_result("v2", cost=0.30),
        _make_claude_result("APPROVED", cost=0.15),
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

    assert result.action_cost == pytest.approx(0.50)
    assert result.initial_qa_cost == pytest.approx(0.25)
    assert result.correction_cost == pytest.approx(0.30)
    assert result.re_review_cost == pytest.approx(0.15)
    assert result.total_cost == pytest.approx(1.20)


@pytest.mark.asyncio
async def test_correction_round_costs_tracked_individually(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """Each CorrectionRound records its own costs."""
    claude.run.side_effect = [
        _make_claude_result("v1", cost=0.10),
        _make_claude_result("ISSUES_FOUND: round1", cost=0.05),
        _make_claude_result("v2", cost=0.20),
        _make_claude_result("ISSUES_FOUND: round2", cost=0.06),
        _make_claude_result("v3", cost=0.25),
        _make_claude_result("APPROVED", cost=0.07),
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
        max_corrections=2,
    )

    assert len(result.corrections) == 2
    assert result.corrections[0].correction_cost == pytest.approx(0.20)
    assert result.corrections[0].re_review_cost == pytest.approx(0.06)
    assert result.corrections[1].correction_cost == pytest.approx(0.25)
    assert result.corrections[1].re_review_cost == pytest.approx(0.07)


# ---------------------------------------------------------------------------
# Document store writes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_documents_saved_to_store_no_correction(
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

    doc_store.write.assert_any_call("result.md", "the output")
    doc_store.write.assert_any_call("qa/result.md", "APPROVED")


@pytest.mark.asyncio
async def test_correction_overwrites_output_document(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When correction runs, the output document is overwritten."""
    claude.run.side_effect = [
        _make_claude_result("v1", cost=0.10),
        _make_claude_result("ISSUES_FOUND: bad", cost=0.05),
        _make_claude_result("v2", cost=0.10),
        _make_claude_result("APPROVED", cost=0.05),
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

    output_writes = [
        call for call in doc_store.write.call_args_list if call[0][0] == "doc.md"
    ]
    assert len(output_writes) == 2
    assert output_writes[0][0][1] == "v1"
    assert output_writes[1][0][1] == "v2"


@pytest.mark.asyncio
async def test_initial_qa_report_preserved_on_correction(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """Initial QA report is saved to _initial path when correction occurs."""
    claude.run.side_effect = [
        _make_claude_result("v1", cost=0.10),
        _make_claude_result("ISSUES_FOUND: problems", cost=0.05),
        _make_claude_result("v2", cost=0.10),
        _make_claude_result("APPROVED", cost=0.05),
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

    doc_store.write.assert_any_call(
        "qa/doc.md_initial", "ISSUES_FOUND: problems"
    )


@pytest.mark.asyncio
async def test_final_qa_report_saved_to_doc_store(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """The final QA report overwrites qa/{name} in the doc store."""
    claude.run.side_effect = [
        _make_claude_result("v1", cost=0.10),
        _make_claude_result("ISSUES_FOUND: fix it", cost=0.05),
        _make_claude_result("v2", cost=0.10),
        _make_claude_result("APPROVED: all fixed", cost=0.05),
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

    qa_report_writes = [
        call
        for call in doc_store.write.call_args_list
        if call[0][0] == "qa/doc.md"
    ]
    # Initial write + final overwrite
    assert len(qa_report_writes) == 2
    assert qa_report_writes[-1][0][1] == "APPROVED: all fixed"


@pytest.mark.asyncio
async def test_round_qa_reports_saved_to_doc_store(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """Each round's QA report is saved with a round suffix."""
    claude.run.side_effect = [
        _make_claude_result("v1", cost=0.10),
        _make_claude_result("ISSUES_FOUND: round1", cost=0.05),
        _make_claude_result("v2", cost=0.10),
        _make_claude_result("ISSUES_FOUND: round2", cost=0.05),
        _make_claude_result("v3", cost=0.10),
        _make_claude_result("APPROVED", cost=0.05),
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
        max_corrections=2,
    )

    doc_store.write.assert_any_call(
        "qa/doc.md_round_1", "ISSUES_FOUND: round2"
    )
    doc_store.write.assert_any_call("qa/doc.md_round_2", "APPROVED")


# ---------------------------------------------------------------------------
# Correction prompt rendering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_correction_uses_session_continuation(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When action agent returns a session_id, correction uses --resume."""
    claude.run.side_effect = [
        _make_claude_result("v1", cost=0.10),
        _make_claude_result("ISSUES_FOUND: fix it", cost=0.05),
        _make_claude_result("v2", cost=0.10),
        _make_claude_result("APPROVED", cost=0.05),
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

    # 3rd claude.run call is the correction — should use session continuation
    correction_call = claude.run.call_args_list[2]
    assert correction_call.kwargs.get("session_id") == "sess-123"
    correction_prompt = correction_call.kwargs.get("prompt", "")
    assert "ISSUES_FOUND" in correction_prompt
    # Session continuation does NOT re-render the full template
    assert prompt_renderer.render_agent_prompt.call_count == 3  # action + QA + re-review


@pytest.mark.asyncio
async def test_correction_falls_back_without_session_id(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """Without a session_id, correction falls back to full re-render."""
    claude.run.side_effect = [
        ClaudeResult(text="v1", cost_usd=0.10, session_id=None, exit_code=0),
        _make_claude_result("ISSUES_FOUND: fix it", cost=0.05),
        _make_claude_result("v2", cost=0.10),
        _make_claude_result("APPROVED", cost=0.05),
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

    # Without session_id, correction uses render_agent_prompt with correction_mode
    correction_render = prompt_renderer.render_agent_prompt.call_args_list[2]
    assert correction_render.kwargs.get("correction_mode") is True
    assert correction_render.kwargs.get("previous_output") == "v1"
    assert "ISSUES_FOUND" in correction_render.kwargs.get("qa_feedback", "")


# ---------------------------------------------------------------------------
# max_corrections=0 (QA is informational only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_corrections_zero_skips_correction(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """With max_corrections=0, QA finds issues but no correction runs."""
    claude.run.side_effect = [
        _make_claude_result("action output", cost=0.15),
        _make_claude_result("ISSUES_FOUND: many problems", cost=0.10),
    ]

    result = await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={"input.md": "requirements"},
        output_doc_name="out.md",
        workspace=Path("/tmp/workspace"),
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        max_corrections=0,
    )

    assert result.output == "action output"
    assert result.corrected is False
    assert result.corrections == []
    assert result.final_qa_report == "ISSUES_FOUND: many problems"
    assert claude.run.call_count == 2


# ---------------------------------------------------------------------------
# Multiple correction rounds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_correction_rounds(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """With max_corrections=2, two correction+re-review rounds can run."""
    claude.run.side_effect = [
        _make_claude_result("v1", cost=0.10),
        _make_claude_result("ISSUES_FOUND: problem A", cost=0.05),
        _make_claude_result("v2", cost=0.15),
        _make_claude_result("ISSUES_FOUND: problem B", cost=0.06),
        _make_claude_result("v3", cost=0.20),
        _make_claude_result("APPROVED: all good", cost=0.07),
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
        max_corrections=2,
    )

    assert result.output == "v3"
    assert result.corrected is True
    assert len(result.corrections) == 2
    assert result.final_qa_report == "APPROVED: all good"
    assert result.initial_qa_report == "ISSUES_FOUND: problem A"
    assert claude.run.call_count == 6


@pytest.mark.asyncio
async def test_loop_exits_early_when_approved(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """With max_corrections=3, loop exits after first approved re-review."""
    claude.run.side_effect = [
        _make_claude_result("v1", cost=0.10),
        _make_claude_result("ISSUES_FOUND: fix this", cost=0.05),
        _make_claude_result("v2", cost=0.15),
        _make_claude_result("APPROVED: fixed", cost=0.06),
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
        max_corrections=3,
    )

    assert result.output == "v2"
    assert len(result.corrections) == 1
    assert result.final_qa_report == "APPROVED: fixed"
    assert claude.run.call_count == 4


@pytest.mark.asyncio
async def test_max_corrections_exhausted_with_issues_remaining(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When max_corrections exhausted, final_qa_report still has issues."""
    claude.run.side_effect = [
        _make_claude_result("v1", cost=0.10),
        _make_claude_result("ISSUES_FOUND: problem", cost=0.05),
        _make_claude_result("v2", cost=0.15),
        _make_claude_result("ISSUES_FOUND: still bad", cost=0.06),
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
        max_corrections=1,
    )

    assert result.output == "v2"
    assert result.corrected is True
    assert len(result.corrections) == 1
    assert "ISSUES_FOUND" in result.final_qa_report


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_action_output_raises_error(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """An empty result from the action agent raises AgentRunError after retry."""
    claude.run.side_effect = [
        _make_claude_result("", cost=0.10),
        _make_claude_result("", cost=0.10),  # retry also empty
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
            empty_retry_delay=0.0,
        )


@pytest.mark.asyncio
async def test_empty_correction_output_raises_error(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """An empty result from the correction run raises AgentRunError after retry."""
    claude.run.side_effect = [
        _make_claude_result("initial output", cost=0.15),
        _make_claude_result("ISSUES_FOUND: fix it", cost=0.10),
        _make_claude_result("   ", cost=0.20),
        _make_claude_result("   ", cost=0.20),  # retry also empty
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
            empty_retry_delay=0.0,
        )


@pytest.mark.asyncio
async def test_empty_qa_output_raises_error(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """An empty result from the QA agent raises AgentRunError after retry."""
    claude.run.side_effect = [
        _make_claude_result("valid action output", cost=0.15),
        _make_claude_result("", cost=0.10),
        _make_claude_result("", cost=0.10),  # retry also empty
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
            empty_retry_delay=0.0,
        )


@pytest.mark.asyncio
async def test_whitespace_only_qa_output_raises_error(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """Whitespace-only QA output raises AgentRunError after retry."""
    claude.run.side_effect = [
        _make_claude_result("valid action output", cost=0.15),
        _make_claude_result("   \n  ", cost=0.10),
        _make_claude_result("   \n  ", cost=0.10),  # retry also whitespace
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
            empty_retry_delay=0.0,
        )


@pytest.mark.asyncio
async def test_empty_re_review_output_raises_error(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """An empty result from the re-review QA run raises AgentRunError after retry."""
    claude.run.side_effect = [
        _make_claude_result("v1", cost=0.10),
        _make_claude_result("ISSUES_FOUND: fix", cost=0.05),
        _make_claude_result("v2", cost=0.10),
        _make_claude_result("", cost=0.05),
        _make_claude_result("", cost=0.05),  # retry also empty
    ]

    with pytest.raises(AgentRunError, match="QA agent returned empty output"):
        await run_qa_cycle(
            claude=claude,
            action_agent=action_agent,
            qa_agent=qa_agent,
            input_docs={},
            output_doc_name="out.md",
            workspace=Path("/tmp/ws"),
            doc_store=doc_store,
            prompt_renderer=prompt_renderer,
            empty_retry_delay=0.0,
        )


# ---------------------------------------------------------------------------
# qa_output_key parameter
# ---------------------------------------------------------------------------


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

    qa_render_call = prompt_renderer.render_agent_prompt.call_args_list[1]
    qa_input_docs = qa_render_call.kwargs.get(
        "input_documents",
        qa_render_call.args[1] if len(qa_render_call.args) > 1 else None,
    )
    assert "integration_guide" in qa_input_docs
    assert qa_input_docs["integration_guide"] == "action output"
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
async def test_qa_output_key_used_in_re_review(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """The re-review QA run also uses qa_output_key for the corrected output."""
    claude.run.side_effect = [
        _make_claude_result("v1", cost=0.10),
        _make_claude_result("ISSUES_FOUND: fix", cost=0.05),
        _make_claude_result("v2", cost=0.10),
        _make_claude_result("APPROVED", cost=0.05),
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

    # With session continuation, render calls are: action + QA + re-review (3 total)
    re_review_call = prompt_renderer.render_agent_prompt.call_args_list[2]
    re_review_input_docs = re_review_call.kwargs.get(
        "input_documents",
        re_review_call.args[1] if len(re_review_call.args) > 1 else None,
    )
    assert "integration_guide" in re_review_input_docs
    assert re_review_input_docs["integration_guide"] == "v2"
    assert "sprint_1_integration" not in re_review_input_docs


# ---------------------------------------------------------------------------
# Empty-output retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_action_output_retries_then_succeeds(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When the action agent returns empty output once, it is retried and succeeds."""
    claude.run.side_effect = [
        _make_claude_result("", cost=0.05),          # empty — triggers retry
        _make_claude_result("real output", cost=0.15),  # retry succeeds
        _make_claude_result("APPROVED", cost=0.10),
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
        empty_retry_delay=0.0,
    )

    assert result.output == "real output"
    assert result.corrected is False
    assert claude.run.call_count == 3


@pytest.mark.asyncio
async def test_empty_action_output_retries_exhausted_raises(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When both the initial and retry action calls return empty, AgentRunError is raised."""
    claude.run.side_effect = [
        _make_claude_result("", cost=0.05),
        _make_claude_result("", cost=0.05),
    ]

    with pytest.raises(AgentRunError, match="empty output"):
        await run_qa_cycle(
            claude=claude,
            action_agent=action_agent,
            qa_agent=qa_agent,
            input_docs={},
            output_doc_name="out.md",
            workspace=Path("/tmp/ws"),
            doc_store=doc_store,
            prompt_renderer=prompt_renderer,
            empty_retry_delay=0.0,
        )

    assert claude.run.call_count == 2


@pytest.mark.asyncio
async def test_empty_qa_output_retries_then_succeeds(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When the QA agent returns empty output once, it is retried and succeeds."""
    claude.run.side_effect = [
        _make_claude_result("action output", cost=0.15),
        _make_claude_result("", cost=0.05),           # empty QA — triggers retry
        _make_claude_result("APPROVED", cost=0.10),   # retry succeeds
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
        empty_retry_delay=0.0,
    )

    assert result.output == "action output"
    assert result.corrected is False
    assert claude.run.call_count == 3


@pytest.mark.asyncio
async def test_empty_correction_output_retries_then_succeeds(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When the correction agent returns empty output once, it is retried and succeeds."""
    claude.run.side_effect = [
        _make_claude_result("v1", cost=0.15),
        _make_claude_result("ISSUES_FOUND: fix it", cost=0.10),
        _make_claude_result("", cost=0.05),           # empty correction — triggers retry
        _make_claude_result("v2", cost=0.20),         # retry succeeds
        _make_claude_result("APPROVED", cost=0.08),
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
        empty_retry_delay=0.0,
    )

    assert result.output == "v2"
    assert result.corrected is True
    assert claude.run.call_count == 5


# ---------------------------------------------------------------------------
# Session ID propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_id_captured_on_result(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """QACycleResult.session_id captures the action agent's session ID."""
    claude.run.side_effect = [
        ClaudeResult(text="output", session_id="action-sess-42", cost_usd=0.10, exit_code=0),
        _make_claude_result("APPROVED", cost=0.05),
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

    assert result.session_id == "action-sess-42"


@pytest.mark.asyncio
async def test_session_id_forwarded_to_runner(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """Resuming a session sends a short 'continue' prompt with --resume, not the
    full re-rendered action prompt (which would re-bill the prior context)."""
    claude.run.side_effect = [
        _make_claude_result("output", cost=0.10),
        _make_claude_result("APPROVED", cost=0.05),
    ]

    await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={},
        output_doc_name="out.md",
        workspace=Path("/tmp/ws"),
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        session_id="resume-sess-99",
    )

    # First claude.run call (action agent) resumes the session...
    first_call = claude.run.call_args_list[0]
    assert first_call.kwargs.get("session_id") == "resume-sess-99"
    # ...with a short "continue" prompt, not the full render.
    action_prompt = first_call.kwargs.get("prompt", "")
    assert "left off" in action_prompt.lower()
    assert action_prompt != "rendered prompt"
    # The action template is NOT re-rendered (only QA is).
    rendered = [
        c.kwargs.get("template_name")
        for c in prompt_renderer.render_agent_prompt.call_args_list
    ]
    assert action_agent.prompt_template not in rendered


@pytest.mark.asyncio
async def test_resume_empty_retry_preserves_session_id(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """If a resumed action run returns empty, the empty-retry still resumes the
    same session rather than restarting fresh and losing the prior context."""
    claude.run.side_effect = [
        _make_claude_result("", cost=0.0),         # first resume attempt: empty
        _make_claude_result("output", cost=0.10),  # empty-retry: ok
        _make_claude_result("APPROVED", cost=0.05),
    ]

    with patch(
        "agentic_dev.orchestrator.qa_cycle.asyncio.sleep", new_callable=AsyncMock,
    ):
        await run_qa_cycle(
            claude=claude,
            action_agent=action_agent,
            qa_agent=qa_agent,
            input_docs={},
            output_doc_name="out.md",
            workspace=Path("/tmp/ws"),
            doc_store=doc_store,
            prompt_renderer=prompt_renderer,
            session_id="resume-sess-99",
        )

    assert claude.run.call_args_list[0].kwargs.get("session_id") == "resume-sess-99"
    assert claude.run.call_args_list[1].kwargs.get("session_id") == "resume-sess-99"


@pytest.mark.asyncio
async def test_session_id_none_by_default(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When no session_id passed, claude.run() is called without it."""
    claude.run.side_effect = [
        _make_claude_result("output", cost=0.10),
        _make_claude_result("APPROVED", cost=0.05),
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

    assert result.session_id == "sess-123"  # from _make_claude_result default


# ---------------------------------------------------------------------------
# on_substep callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_substep_called_before_qa(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """on_substep('qa') fires after action agent, before QA runs."""
    claude.run.side_effect = [
        _make_claude_result("action output", cost=0.15),
        _make_claude_result("APPROVED", cost=0.10),
    ]
    calls = []

    await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={},
        output_doc_name="out.md",
        workspace=Path("/tmp/ws"),
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        on_substep=lambda step: calls.append(step),
    )

    assert "qa" in calls


@pytest.mark.asyncio
async def test_on_substep_called_before_correction(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """on_substep('correction') fires when QA finds issues, before correction runs."""
    claude.run.side_effect = [
        _make_claude_result("v1", cost=0.15),
        _make_claude_result("ISSUES_FOUND: fix it", cost=0.10),
        _make_claude_result("v2", cost=0.20),
        _make_claude_result("APPROVED", cost=0.08),
    ]
    calls = []

    await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={},
        output_doc_name="out.md",
        workspace=Path("/tmp/ws"),
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        on_substep=lambda step: calls.append(step),
    )

    assert calls == ["qa", "correction"]


@pytest.mark.asyncio
async def test_on_substep_not_called_when_none(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """No error when on_substep is None (default)."""
    claude.run.side_effect = [
        _make_claude_result("output", cost=0.15),
        _make_claude_result("APPROVED", cost=0.10),
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

    assert result.output == "output"  # just confirm it works without callback


# ---------------------------------------------------------------------------
# skip_to_correction (resume mid-QA-cycle)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skip_to_correction_loads_existing_output_and_runs_correction(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When skip_to_correction=True, action+QA are skipped and correction runs."""
    # doc_store already has the action output and QA report from prior run
    doc_store.read = MagicMock(side_effect=lambda name: {
        "out.md": "prior action output",
        "qa/out.md": "ISSUES_FOUND: fix the bug",
    }[name])
    doc_store.exists = MagicMock(return_value=True)

    claude.run.side_effect = [
        _make_claude_result("corrected output", cost=0.20),  # correction
        _make_claude_result("APPROVED", cost=0.08),           # re-review
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
        skip_to_correction=True,
    )

    assert result.output == "corrected output"
    assert result.corrected is True
    assert result.initial_qa_report == "ISSUES_FOUND: fix the bug"
    assert claude.run.call_count == 2  # correction + re-review only


@pytest.mark.asyncio
async def test_skip_to_correction_no_issues_returns_existing(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When skip_to_correction=True but QA report has no issues, no correction runs."""
    doc_store.read = MagicMock(side_effect=lambda name: {
        "out.md": "prior action output",
        "qa/out.md": "APPROVED: all good",
    }[name])
    doc_store.exists = MagicMock(return_value=True)

    result = await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={},
        output_doc_name="out.md",
        workspace=Path("/tmp/ws"),
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        skip_to_correction=True,
    )

    assert result.output == "prior action output"
    assert result.corrected is False
    assert claude.run.call_count == 0


# ---------------------------------------------------------------------------
# MCP config passthrough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_config_passed_to_action_agent(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When mcp_config is provided, it flows to the action agent config."""
    claude.run.side_effect = [
        _make_claude_result("action output", cost=0.15),
        _make_claude_result("APPROVED", cost=0.10),
    ]
    fake_mcp_path = Path("/fake/mcp_config.json")

    await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={},
        output_doc_name="out.md",
        workspace=Path("/tmp/ws"),
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        mcp_config=fake_mcp_path,
    )

    # Action agent (first call) should have mcp_config
    action_call_agent = claude.run.call_args_list[0].kwargs["agent"]
    assert action_call_agent.mcp_config == fake_mcp_path

    # QA agent (second call) should NOT have mcp_config
    qa_call_agent = claude.run.call_args_list[1].kwargs["agent"]
    assert qa_call_agent.mcp_config is None


@pytest.mark.asyncio
async def test_mcp_config_defaults_to_none(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When mcp_config is not provided, action agent has mcp_config=None."""
    claude.run.side_effect = [
        _make_claude_result("output", cost=0.10),
        _make_claude_result("APPROVED", cost=0.05),
    ]

    await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={},
        output_doc_name="out.md",
        workspace=Path("/tmp/ws"),
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
    )

    action_call_agent = claude.run.call_args_list[0].kwargs["agent"]
    assert action_call_agent.mcp_config is None


# ---------------------------------------------------------------------------
# Content-marker recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_content_markers_triggers_recovery_when_missing(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When action output lacks content markers, recover from session JSONL."""
    spec_text = "# Frontend Spec\n## Tech Stack\n- Framework: React"
    claude.run.side_effect = [
        ClaudeResult(
            text="The spec is rendered above.",
            session_id="sess-recover",
            cost_usd=0.50,
            exit_code=0,
        ),
        _make_claude_result("APPROVED", cost=0.10),
    ]

    with patch.object(
        ClaudeRunner, "_recover_longest_from_session", return_value=spec_text,
    ) as mock_recover:
        result = await run_qa_cycle(
            claude=claude,
            action_agent=action_agent,
            qa_agent=qa_agent,
            input_docs={},
            output_doc_name="frontend_spec",
            workspace=Path("/tmp/ws"),
            doc_store=doc_store,
            prompt_renderer=prompt_renderer,
            content_markers=["# Frontend Spec"],
        )

    mock_recover.assert_called_once_with("sess-recover", Path("/tmp/ws"))
    assert result.output == spec_text


@pytest.mark.asyncio
async def test_content_markers_no_recovery_when_present(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When action output already has the markers, skip recovery."""
    spec_text = "# Frontend Spec\n## Tech Stack\n- Framework: React"
    claude.run.side_effect = [
        ClaudeResult(
            text=spec_text,
            session_id="sess-ok",
            cost_usd=0.50,
            exit_code=0,
        ),
        _make_claude_result("APPROVED", cost=0.10),
    ]

    with patch.object(
        ClaudeRunner, "_recover_longest_from_session",
    ) as mock_recover:
        result = await run_qa_cycle(
            claude=claude,
            action_agent=action_agent,
            qa_agent=qa_agent,
            input_docs={},
            output_doc_name="frontend_spec",
            workspace=Path("/tmp/ws"),
            doc_store=doc_store,
            prompt_renderer=prompt_renderer,
            content_markers=["# Frontend Spec"],
        )

    mock_recover.assert_not_called()
    assert result.output == spec_text


@pytest.mark.asyncio
async def test_content_markers_skipped_when_recovered_lacks_markers(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When recovered text also lacks markers, keep the original output."""
    claude.run.side_effect = [
        ClaudeResult(
            text="Summary of work done.",
            session_id="sess-no-match",
            cost_usd=0.50,
            exit_code=0,
        ),
        _make_claude_result("APPROVED", cost=0.10),
    ]

    with patch.object(
        ClaudeRunner, "_recover_longest_from_session",
        return_value="Some other long text without the marker.",
    ):
        result = await run_qa_cycle(
            claude=claude,
            action_agent=action_agent,
            qa_agent=qa_agent,
            input_docs={},
            output_doc_name="frontend_spec",
            workspace=Path("/tmp/ws"),
            doc_store=doc_store,
            prompt_renderer=prompt_renderer,
            content_markers=["# Frontend Spec"],
        )

    assert result.output == "Summary of work done."


@pytest.mark.asyncio
async def test_content_markers_none_skips_check(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When content_markers is None (default), no recovery is attempted."""
    claude.run.side_effect = [
        _make_claude_result("action output", cost=0.15),
        _make_claude_result("APPROVED", cost=0.10),
    ]

    with patch.object(
        ClaudeRunner, "_recover_longest_from_session",
    ) as mock_recover:
        result = await run_qa_cycle(
            claude=claude,
            action_agent=action_agent,
            qa_agent=qa_agent,
            input_docs={},
            output_doc_name="result.md",
            workspace=Path("/tmp/ws"),
            doc_store=doc_store,
            prompt_renderer=prompt_renderer,
        )

    mock_recover.assert_not_called()
    assert result.output == "action output"


@pytest.mark.asyncio
async def test_content_markers_no_session_id_skips_recovery(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When action result has no session_id, skip recovery even with markers."""
    claude.run.side_effect = [
        ClaudeResult(
            text="Summary without markers.",
            session_id=None,
            cost_usd=0.50,
            exit_code=0,
        ),
        _make_claude_result("APPROVED", cost=0.10),
    ]

    with patch.object(
        ClaudeRunner, "_recover_longest_from_session",
    ) as mock_recover:
        result = await run_qa_cycle(
            claude=claude,
            action_agent=action_agent,
            qa_agent=qa_agent,
            input_docs={},
            output_doc_name="frontend_spec",
            workspace=Path("/tmp/ws"),
            doc_store=doc_store,
            prompt_renderer=prompt_renderer,
            content_markers=["# Frontend Spec"],
        )

    mock_recover.assert_not_called()
    assert result.output == "Summary without markers."


# ---------------------------------------------------------------------------
# skip_action_output_in_qa (R5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skip_action_output_excludes_output_from_qa_input(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """When skip_action_output_in_qa=True, QA input docs should NOT contain
    the action output text."""
    claude.run.side_effect = [
        _make_claude_result("action output", cost=0.15),
        _make_claude_result("APPROVED", cost=0.10),
    ]

    await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={"spec": "backend spec content"},
        output_doc_name="sprint_1_backend",
        workspace=Path("/tmp/ws"),
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        skip_action_output_in_qa=True,
    )

    qa_render_call = prompt_renderer.render_agent_prompt.call_args_list[1]
    qa_input_docs = qa_render_call.kwargs.get(
        "input_documents",
        qa_render_call.args[1] if len(qa_render_call.args) > 1 else None,
    )
    assert "sprint_1_backend" not in qa_input_docs
    assert "spec" in qa_input_docs


@pytest.mark.asyncio
async def test_skip_action_output_false_includes_output_in_qa_input(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """Default behavior (skip_action_output_in_qa=False) includes action output."""
    claude.run.side_effect = [
        _make_claude_result("action output", cost=0.15),
        _make_claude_result("APPROVED", cost=0.10),
    ]

    await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={"spec": "backend spec content"},
        output_doc_name="sprint_1_backend",
        workspace=Path("/tmp/ws"),
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        skip_action_output_in_qa=False,
    )

    qa_render_call = prompt_renderer.render_agent_prompt.call_args_list[1]
    qa_input_docs = qa_render_call.kwargs.get(
        "input_documents",
        qa_render_call.args[1] if len(qa_render_call.args) > 1 else None,
    )
    assert "sprint_1_backend" in qa_input_docs
    assert qa_input_docs["sprint_1_backend"] == "action output"


@pytest.mark.asyncio
async def test_skip_action_output_also_applies_to_re_review(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """skip_action_output_in_qa should also apply to re-review after correction."""
    claude.run.side_effect = [
        _make_claude_result("v1", cost=0.10),
        _make_claude_result("ISSUES_FOUND: fix", cost=0.05),
        _make_claude_result("v2", cost=0.10),
        _make_claude_result("APPROVED", cost=0.05),
    ]

    await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={"spec": "content"},
        output_doc_name="sprint_1_backend",
        workspace=Path("/tmp/ws"),
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        skip_action_output_in_qa=True,
    )

    # Re-review is the 3rd render call (action + QA + re-review, correction uses session)
    re_review_call = prompt_renderer.render_agent_prompt.call_args_list[2]
    re_review_input_docs = re_review_call.kwargs.get(
        "input_documents",
        re_review_call.args[1] if len(re_review_call.args) > 1 else None,
    )
    assert "sprint_1_backend" not in re_review_input_docs


# ---------------------------------------------------------------------------
# Budget enforcement (R7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_warning_emitted_when_cost_exceeds_budget(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """A BudgetWarningEvent should be emitted when cost exceeds max_budget_usd."""
    action_agent.claude.max_budget_usd = 0.10
    claude.run.side_effect = [
        _make_claude_result("output", cost=0.50),
        _make_claude_result("APPROVED", cost=0.05),
    ]

    with patch("agentic_dev.orchestrator.qa_cycle.emit") as mock_emit:
        await run_qa_cycle(
            claude=claude,
            action_agent=action_agent,
            qa_agent=qa_agent,
            input_docs={"input.md": "reqs"},
            output_doc_name="out.md",
            workspace=Path("/tmp/ws"),
            doc_store=doc_store,
            prompt_renderer=prompt_renderer,
        )

        budget_warnings = [
            c for c in mock_emit.call_args_list
            if hasattr(c[0][1], "event_type") and c[0][1].event_type == "budget_warning"
        ]
        assert len(budget_warnings) == 1
        event = budget_warnings[0][0][1]
        assert event.agent_name == "action_agent"
        assert event.cost_usd == 0.50
        assert event.max_budget_usd == 0.10


@pytest.mark.asyncio
async def test_no_budget_warning_when_cost_within_budget(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """No BudgetWarningEvent when cost is within max_budget_usd."""
    action_agent.claude.max_budget_usd = 5.00
    claude.run.side_effect = [
        _make_claude_result("output", cost=0.50),
        _make_claude_result("APPROVED", cost=0.05),
    ]

    with patch("agentic_dev.orchestrator.qa_cycle.emit") as mock_emit:
        await run_qa_cycle(
            claude=claude,
            action_agent=action_agent,
            qa_agent=qa_agent,
            input_docs={"input.md": "reqs"},
            output_doc_name="out.md",
            workspace=Path("/tmp/ws"),
            doc_store=doc_store,
            prompt_renderer=prompt_renderer,
        )

        budget_warnings = [
            c for c in mock_emit.call_args_list
            if hasattr(c[0][1], "event_type") and c[0][1].event_type == "budget_warning"
        ]
        assert len(budget_warnings) == 0


@pytest.mark.asyncio
async def test_budget_warning_does_not_stop_execution(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """Budget warning is informational — execution continues normally."""
    action_agent.claude.max_budget_usd = 0.01
    claude.run.side_effect = [
        _make_claude_result("output", cost=0.50),
        _make_claude_result("APPROVED", cost=0.05),
    ]

    result = await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={"input.md": "reqs"},
        output_doc_name="out.md",
        workspace=Path("/tmp/ws"),
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
    )

    assert result.output == "output"
    assert result.action_cost == 0.50
