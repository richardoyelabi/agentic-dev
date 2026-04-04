"""Tests for the QA cycle orchestrator."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_dev.agents.base import AgentDefinition, ClaudeConfig
from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import AgentRunError
from agentic_dev.orchestrator.qa_cycle import run_qa_cycle, CorrectionRound
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
        "qa_reports/doc.md_initial", "ISSUES_FOUND: problems"
    )


@pytest.mark.asyncio
async def test_final_qa_report_saved_to_doc_store(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """The final QA report overwrites qa_reports/{name} in the doc store."""
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
        if call[0][0] == "qa_reports/doc.md"
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
        "qa_reports/doc.md_round_1", "ISSUES_FOUND: round2"
    )
    doc_store.write.assert_any_call("qa_reports/doc.md_round_2", "APPROVED")


# ---------------------------------------------------------------------------
# Correction prompt rendering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_correction_prompt_uses_correction_mode(
    claude, action_agent, qa_agent, doc_store, prompt_renderer
):
    """The correction run renders the prompt with correction_mode=True."""
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

    # 3rd call to render_agent_prompt is the correction
    correction_call = prompt_renderer.render_agent_prompt.call_args_list[2]
    assert correction_call.kwargs.get("correction_mode") is True
    assert correction_call.kwargs.get("previous_output") == "v1"
    assert "ISSUES_FOUND" in correction_call.kwargs.get("qa_feedback", "")


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

    # 4th render call is the re-review QA
    re_review_call = prompt_renderer.render_agent_prompt.call_args_list[3]
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
    """When session_id is passed, it is forwarded to claude.run()."""
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

    # First claude.run call (action agent) should have session_id
    first_call = claude.run.call_args_list[0]
    assert first_call.kwargs.get("session_id") == "resume-sess-99"


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


