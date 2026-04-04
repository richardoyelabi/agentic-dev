"""Tests for the sprint runner."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_dev.agents.base import AgentDefinition, ClaudeConfig
from agentic_dev.agents.registry import AgentRegistry
from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import AgentRunError
from agentic_dev.orchestrator.sprint_runner import SprintResult, SprintRunner
from agentic_dev.prompts.renderer import PromptRenderer


def _make_agent(name: str, template: str = "tpl.md.j2") -> AgentDefinition:
    """Helper to build a minimal AgentDefinition."""
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
def claude() -> ClaudeRunner:
    runner = MagicMock(spec=ClaudeRunner)
    runner.run = AsyncMock()
    return runner


@pytest.fixture
def registry() -> AgentRegistry:
    reg = MagicMock(spec=AgentRegistry)
    reg.get = MagicMock(side_effect=lambda name: _make_agent(name))
    return reg


@pytest.fixture
def doc_store() -> DocumentStore:
    store = MagicMock(spec=DocumentStore)
    store.read = MagicMock(side_effect=lambda name: f"content of {name}")
    return store


@pytest.fixture
def prompt_renderer() -> PromptRenderer:
    renderer = MagicMock(spec=PromptRenderer)
    renderer.render_agent_prompt = MagicMock(return_value="rendered prompt")
    return renderer


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path / "project"


@pytest.fixture
def runner(claude, registry, doc_store, prompt_renderer, project_dir) -> SprintRunner:
    return SprintRunner(
        claude=claude,
        registry=registry,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        project_dir=project_dir,
    )


@pytest.fixture
def frontend_only_runner(claude, registry, doc_store, prompt_renderer, project_dir) -> SprintRunner:
    return SprintRunner(
        claude=claude,
        registry=registry,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        project_dir=project_dir,
        project_type="frontend_only",
    )


@pytest.fixture
def backend_only_runner(claude, registry, doc_store, prompt_renderer, project_dir) -> SprintRunner:
    return SprintRunner(
        claude=claude,
        registry=registry,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        project_dir=project_dir,
        project_type="backend_only",
    )


@pytest.mark.asyncio
async def test_successful_sprint_backend_and_frontend(runner, claude):
    """A sprint without integration runs backend and frontend QA cycles."""
    # backend: action + QA (no issues)
    # frontend: action + QA (no issues)
    claude.run.side_effect = [
        _make_claude_result("backend code", cost=0.20),
        _make_claude_result("APPROVED", cost=0.10),
        _make_claude_result("frontend code", cost=0.25),
        _make_claude_result("APPROVED", cost=0.10),
    ]

    result = await runner.run_sprint(sprint_number=1, sprint_scope="auth feature")

    assert isinstance(result, SprintResult)
    assert result.sprint_number == 1
    assert result.success is True
    assert result.integration_result is None
    assert result.total_cost == pytest.approx(0.65)
    assert claude.run.call_count == 4


@pytest.mark.asyncio
async def test_sprint_with_integration(runner, claude):
    """When needs_integration is True, an integration QA cycle runs too."""
    claude.run.side_effect = [
        _make_claude_result("backend code", cost=0.20),
        _make_claude_result("APPROVED", cost=0.10),
        _make_claude_result("frontend code", cost=0.25),
        _make_claude_result("APPROVED", cost=0.10),
        _make_claude_result("integration code", cost=0.30),
        _make_claude_result("APPROVED", cost=0.15),
    ]

    result = await runner.run_sprint(
        sprint_number=2, sprint_scope="payment", needs_integration=True
    )

    assert result.success is True
    assert result.integration_result is not None
    assert result.total_cost == pytest.approx(1.10)
    assert claude.run.call_count == 6


@pytest.mark.asyncio
async def test_sprint_failure_returns_failed_result(runner, claude):
    """An AgentRunError from claude.run is caught and returned as a failed SprintResult."""
    claude.run.side_effect = AgentRunError(
        agent_name="backend_developer",
        message="CLI crashed",
        exit_code=1,
    )

    result = await runner.run_sprint(sprint_number=1, sprint_scope="scope")

    assert result.success is False
    assert result.sprint_number == 1
    assert "backend_developer" in result.error


@pytest.mark.asyncio
async def test_partial_cost_preserved_on_frontend_failure(runner, claude):
    """When the frontend agent fails after backend succeeds, backend cost is preserved."""
    claude.run.side_effect = [
        # Backend succeeds
        _make_claude_result("backend code", cost=0.20),
        _make_claude_result("APPROVED", cost=0.10),
        # Frontend action agent crashes
        AgentRunError(agent_name="frontend_developer", message="timeout"),
    ]

    result = await runner.run_sprint(sprint_number=1, sprint_scope="scope")

    assert result.success is False
    # Backend costs (0.20 + 0.10) must be preserved even though frontend failed
    assert result.total_cost == pytest.approx(0.30)


@pytest.mark.asyncio
async def test_costs_aggregated_with_corrections(runner, claude):
    """Costs include correction runs when QA finds issues."""
    claude.run.side_effect = [
        # Backend: action -> QA finds issues -> correction -> re-review
        _make_claude_result("backend v1", cost=0.20),
        _make_claude_result("ISSUES_FOUND: fix error handling", cost=0.10),
        _make_claude_result("backend v2", cost=0.25),
        _make_claude_result("APPROVED after fix", cost=0.08),
        # Frontend: action -> QA approves
        _make_claude_result("frontend code", cost=0.30),
        _make_claude_result("APPROVED", cost=0.15),
    ]

    result = await runner.run_sprint(sprint_number=1, sprint_scope="scope")

    assert result.success is True
    assert result.backend_result.corrected is True
    assert result.frontend_result.corrected is False
    # 0.20 + 0.10 + 0.25 + 0.08 + 0.30 + 0.15 = 1.08
    assert result.total_cost == pytest.approx(1.08)


@pytest.mark.asyncio
async def test_doc_store_reads_specs(runner, claude, doc_store):
    """Sprint runner reads backend_spec, frontend_spec, and api_contract."""
    claude.run.side_effect = [
        _make_claude_result("backend", cost=0.10),
        _make_claude_result("APPROVED", cost=0.05),
        _make_claude_result("frontend", cost=0.10),
        _make_claude_result("APPROVED", cost=0.05),
    ]

    await runner.run_sprint(sprint_number=1, sprint_scope="scope")

    doc_store.read.assert_any_call("backend_spec")
    doc_store.read.assert_any_call("frontend_spec")
    doc_store.read.assert_any_call("api_contract")


@pytest.mark.asyncio
async def test_frontend_only_skips_backend_cycle(frontend_only_runner, claude, doc_store):
    """frontend_only project type runs only the frontend QA cycle."""
    claude.run.side_effect = [
        _make_claude_result("frontend code", cost=0.25),
        _make_claude_result("APPROVED", cost=0.10),
    ]

    result = await frontend_only_runner.run_sprint(sprint_number=1, sprint_scope="ui feature")

    assert result.success is True
    assert result.backend_result is None
    assert result.frontend_result is not None
    assert result.total_cost == pytest.approx(0.35)
    assert claude.run.call_count == 2
    # Should NOT read backend_spec
    read_calls = [call.args[0] for call in doc_store.read.call_args_list]
    assert "backend_spec" not in read_calls


@pytest.mark.asyncio
async def test_backend_only_skips_frontend_cycle(backend_only_runner, claude, doc_store):
    """backend_only project type runs only the backend QA cycle."""
    claude.run.side_effect = [
        _make_claude_result("backend code", cost=0.20),
        _make_claude_result("APPROVED", cost=0.10),
    ]

    result = await backend_only_runner.run_sprint(sprint_number=1, sprint_scope="api endpoint")

    assert result.success is True
    assert result.frontend_result is None
    assert result.backend_result is not None
    assert result.total_cost == pytest.approx(0.30)
    assert claude.run.call_count == 2
    # Should NOT read frontend_spec
    read_calls = [call.args[0] for call in doc_store.read.call_args_list]
    assert "frontend_spec" not in read_calls


@pytest.mark.asyncio
async def test_frontend_only_passes_empty_api_contract(frontend_only_runner, claude, prompt_renderer):
    """frontend_only projects pass empty string for api_contract to templates."""
    claude.run.side_effect = [
        _make_claude_result("frontend code", cost=0.25),
        _make_claude_result("APPROVED", cost=0.10),
    ]

    await frontend_only_runner.run_sprint(sprint_number=1, sprint_scope="ui feature")

    # Check the input_docs passed to render_agent_prompt
    action_call = prompt_renderer.render_agent_prompt.call_args_list[0]
    input_docs = action_call.kwargs.get("input_documents") or action_call.args[1]
    assert input_docs["api_contract"] == ""


@pytest.mark.asyncio
async def test_default_project_type_runs_both_cycles(runner, claude):
    """Default (no project_type) runs both backend and frontend cycles."""
    claude.run.side_effect = [
        _make_claude_result("backend code", cost=0.20),
        _make_claude_result("APPROVED", cost=0.10),
        _make_claude_result("frontend code", cost=0.25),
        _make_claude_result("APPROVED", cost=0.10),
    ]

    result = await runner.run_sprint(sprint_number=1, sprint_scope="auth feature")

    assert result.backend_result is not None
    assert result.frontend_result is not None
    assert claude.run.call_count == 4
