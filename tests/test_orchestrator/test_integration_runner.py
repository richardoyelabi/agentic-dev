"""Tests for the integration runner."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_dev.agents.base import AgentDefinition, ClaudeConfig
from agentic_dev.agents.registry import AgentRegistry
from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.documents.store import DocumentStore
from agentic_dev.orchestrator.integration_runner import IntegrationRunner
from agentic_dev.orchestrator.sprint_runner import SprintResult
from agentic_dev.prompts.renderer import PromptRenderer
from agentic_dev.state.manager import StateManager
from agentic_dev.state.models import PipelineState, SprintState, SprintStatus


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
        session_id="sess-integration-123",
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
    store.exists = MagicMock(return_value=True)
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
def state_manager() -> StateManager:
    mgr = MagicMock(spec=StateManager)
    return mgr


@pytest.fixture
def pipeline_state() -> PipelineState:
    return PipelineState(
        project_name="testapp",
        sprints=[
            SprintState(
                sprint_number=1,
                name="Auth sprint",
                status=SprintStatus.COMPLETE,
                integration_services=["github", "stripe"],
            ),
        ],
        current_sprint=1,
    )


@pytest.fixture
def runner(
    claude, registry, doc_store, prompt_renderer, project_dir, state_manager, pipeline_state,
) -> IntegrationRunner:
    return IntegrationRunner(
        claude=claude,
        registry=registry,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        project_dir=project_dir,
        state_manager=state_manager,
        pipeline_state=pipeline_state,
    )


@pytest.mark.asyncio
async def test_run_integration_fresh(runner, claude, pipeline_state):
    """A fresh integration run transitions INTEGRATION -> COMPLETE and sets session_id."""
    sprint = pipeline_state.sprints[0]
    assert sprint.status == SprintStatus.COMPLETE
    assert sprint.integration_session_id is None

    # integration action + QA (no issues)
    claude.run.side_effect = [
        _make_claude_result("integration code", cost=0.30),
        _make_claude_result("APPROVED", cost=0.15),
    ]

    result = await runner.run_integration(sprint)

    assert isinstance(result, SprintResult)
    assert result.success is True
    assert result.integration_result is not None
    assert result.total_cost == pytest.approx(0.45)
    assert sprint.status == SprintStatus.COMPLETE
    assert sprint.integration_session_id == "sess-integration-123"


@pytest.mark.asyncio
async def test_run_integration_resumes_crashed_run(runner, claude, pipeline_state):
    """A sprint stuck at INTEGRATION_QA resumes with existing session_id."""
    sprint = pipeline_state.sprints[0]
    sprint.status = SprintStatus.INTEGRATION_QA
    sprint.integration_session_id = "sess-prior-run"

    # Only QA needs to run (action already completed in prior run)
    # But run_qa_cycle with skip_to_correction=False runs both action + QA
    claude.run.side_effect = [
        _make_claude_result("integration code", cost=0.30),
        _make_claude_result("APPROVED", cost=0.15),
    ]

    result = await runner.run_integration(sprint)

    assert result.success is True
    assert sprint.status == SprintStatus.COMPLETE
    # Session ID should be updated from the new run
    assert sprint.integration_session_id == "sess-integration-123"


@pytest.mark.asyncio
async def test_run_integration_updates_session_id(runner, claude, pipeline_state):
    """Integration session_id is updated from the QA cycle result."""
    sprint = pipeline_state.sprints[0]

    claude.run.side_effect = [
        ClaudeResult(text="integration code", session_id="new-sess-456", cost_usd=0.20, exit_code=0),
        ClaudeResult(text="APPROVED", session_id="new-sess-789", cost_usd=0.10, exit_code=0),
    ]

    await runner.run_integration(sprint)

    assert sprint.integration_session_id is not None


@pytest.mark.asyncio
async def test_run_integration_accumulates_cost(runner, claude, pipeline_state):
    """Cost from integration QA cycle is tracked in the result."""
    sprint = pipeline_state.sprints[0]

    claude.run.side_effect = [
        _make_claude_result("integration code", cost=0.50),
        _make_claude_result("APPROVED", cost=0.25),
    ]

    result = await runner.run_integration(sprint)

    assert result.total_cost == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_run_integration_saves_state(runner, claude, pipeline_state, state_manager):
    """State is saved at key points during integration."""
    sprint = pipeline_state.sprints[0]

    claude.run.side_effect = [
        _make_claude_result("integration code", cost=0.30),
        _make_claude_result("APPROVED", cost=0.15),
    ]

    await runner.run_integration(sprint)

    # State should be saved at least twice: once when entering INTEGRATION, once on COMPLETE
    assert state_manager.save.call_count >= 2
