"""Tests for the sprint runner."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_dev.agents.base import AgentDefinition, ClaudeConfig
from agentic_dev.agents.registry import AgentRegistry
from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import AgentRunError, RateLimitError
from agentic_dev.orchestrator.sprint_runner import SprintResult, SprintRunner, _should_skip
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
async def test_sprint_propagates_rate_limit_error(runner, claude):
    """RateLimitError is re-raised rather than wrapped in a failed SprintResult.

    The engine handles rate limits via a pause-and-resume path, not by treating
    them as sprint failures — wrapping them in a ``SprintResult`` would destroy
    the type information the engine needs.
    """
    claude.run.side_effect = RateLimitError(
        agent_name="backend_developer",
        message="Rate limited after 6 attempts",
        attempts=6,
        exit_code=1,
    )

    with pytest.raises(RateLimitError) as exc_info:
        await runner.run_sprint(sprint_number=1, sprint_scope="scope")

    assert exc_info.value.agent_name == "backend_developer"
    assert exc_info.value.attempts == 6


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


class TestShouldSkip:
    """Tests for the _should_skip function used in sub-step checkpointing."""

    def test_pending_skips_nothing(self):
        assert _should_skip(SprintStatus.PENDING, SprintStatus.BACKEND_DEV) is False
        assert _should_skip(SprintStatus.PENDING, SprintStatus.FRONTEND_DEV) is False
        assert _should_skip(SprintStatus.PENDING, SprintStatus.INTEGRATION) is False

    def test_backend_dev_skips_nothing(self):
        assert _should_skip(SprintStatus.BACKEND_DEV, SprintStatus.BACKEND_DEV) is False

    def test_backend_qa_skips_backend_dev(self):
        """BACKEND_QA skips BACKEND_DEV — dev is done, now doing QA."""
        assert _should_skip(SprintStatus.BACKEND_QA, SprintStatus.BACKEND_DEV) is True
        assert _should_skip(SprintStatus.BACKEND_QA, SprintStatus.BACKEND_QA) is False

    def test_backend_correction_skips_backend_dev_and_qa(self):
        """BACKEND_CORRECTION skips BACKEND_DEV and BACKEND_QA."""
        assert _should_skip(SprintStatus.BACKEND_CORRECTION, SprintStatus.BACKEND_DEV) is True
        assert _should_skip(SprintStatus.BACKEND_CORRECTION, SprintStatus.BACKEND_QA) is True
        assert _should_skip(SprintStatus.BACKEND_CORRECTION, SprintStatus.BACKEND_CORRECTION) is False

    def test_frontend_dev_skips_all_backend(self):
        """FRONTEND_DEV skips all backend sub-steps."""
        assert _should_skip(SprintStatus.FRONTEND_DEV, SprintStatus.BACKEND_DEV) is True
        assert _should_skip(SprintStatus.FRONTEND_DEV, SprintStatus.BACKEND_QA) is True
        assert _should_skip(SprintStatus.FRONTEND_DEV, SprintStatus.BACKEND_CORRECTION) is True
        assert _should_skip(SprintStatus.FRONTEND_DEV, SprintStatus.FRONTEND_DEV) is False

    def test_frontend_qa_skips_backend_and_frontend_dev(self):
        """FRONTEND_QA skips all backend and FRONTEND_DEV."""
        assert _should_skip(SprintStatus.FRONTEND_QA, SprintStatus.BACKEND_DEV) is True
        assert _should_skip(SprintStatus.FRONTEND_QA, SprintStatus.FRONTEND_DEV) is True
        assert _should_skip(SprintStatus.FRONTEND_QA, SprintStatus.FRONTEND_QA) is False

    def test_frontend_correction_skips_through_frontend_qa(self):
        """FRONTEND_CORRECTION skips everything up to and including FRONTEND_QA."""
        assert _should_skip(SprintStatus.FRONTEND_CORRECTION, SprintStatus.BACKEND_DEV) is True
        assert _should_skip(SprintStatus.FRONTEND_CORRECTION, SprintStatus.FRONTEND_DEV) is True
        assert _should_skip(SprintStatus.FRONTEND_CORRECTION, SprintStatus.FRONTEND_QA) is True
        assert _should_skip(SprintStatus.FRONTEND_CORRECTION, SprintStatus.FRONTEND_CORRECTION) is False

    def test_integration_skips_backend_and_frontend(self):
        """INTEGRATION skips all backend and frontend sub-steps."""
        assert _should_skip(SprintStatus.INTEGRATION, SprintStatus.BACKEND_DEV) is True
        assert _should_skip(SprintStatus.INTEGRATION, SprintStatus.FRONTEND_DEV) is True
        assert _should_skip(SprintStatus.INTEGRATION, SprintStatus.FRONTEND_CORRECTION) is True
        assert _should_skip(SprintStatus.INTEGRATION, SprintStatus.INTEGRATION) is False

    def test_integration_qa_skips_through_integration_dev(self):
        assert _should_skip(SprintStatus.INTEGRATION_QA, SprintStatus.BACKEND_DEV) is True
        assert _should_skip(SprintStatus.INTEGRATION_QA, SprintStatus.FRONTEND_DEV) is True
        assert _should_skip(SprintStatus.INTEGRATION_QA, SprintStatus.INTEGRATION) is True
        assert _should_skip(SprintStatus.INTEGRATION_QA, SprintStatus.INTEGRATION_QA) is False

    def test_integration_correction_skips_through_integration_qa(self):
        assert _should_skip(SprintStatus.INTEGRATION_CORRECTION, SprintStatus.BACKEND_DEV) is True
        assert _should_skip(SprintStatus.INTEGRATION_CORRECTION, SprintStatus.FRONTEND_DEV) is True
        assert _should_skip(SprintStatus.INTEGRATION_CORRECTION, SprintStatus.INTEGRATION) is True
        assert _should_skip(SprintStatus.INTEGRATION_CORRECTION, SprintStatus.INTEGRATION_QA) is True
        assert _should_skip(SprintStatus.INTEGRATION_CORRECTION, SprintStatus.INTEGRATION_CORRECTION) is False

    def test_complete_skips_everything(self):
        assert _should_skip(SprintStatus.COMPLETE, SprintStatus.BACKEND_DEV) is True
        assert _should_skip(SprintStatus.COMPLETE, SprintStatus.FRONTEND_DEV) is True
        assert _should_skip(SprintStatus.COMPLETE, SprintStatus.INTEGRATION) is True

    def test_failed_skips_nothing(self):
        """FAILED is order 0 — resume logic restores the sub-step first."""
        assert _should_skip(SprintStatus.FAILED, SprintStatus.BACKEND_DEV) is False
        assert _should_skip(SprintStatus.FAILED, SprintStatus.FRONTEND_DEV) is False
        assert _should_skip(SprintStatus.FAILED, SprintStatus.INTEGRATION) is False


class TestSubStepCheckpointing:
    """Tests for sub-step state saves and skip-on-resume in SprintRunner."""

    @pytest.fixture
    def state_manager(self):
        mgr = MagicMock(spec=StateManager)
        return mgr

    @pytest.fixture
    def pipeline_state(self):
        return PipelineState(project_name="test")

    @pytest.fixture
    def checkpointing_runner(
        self, claude, registry, doc_store, prompt_renderer, project_dir,
        state_manager, pipeline_state,
    ):
        return SprintRunner(
            claude=claude,
            registry=registry,
            doc_store=doc_store,
            prompt_renderer=prompt_renderer,
            project_dir=project_dir,
            state_manager=state_manager,
            pipeline_state=pipeline_state,
        )

    @pytest.fixture
    def claude(self):
        runner = MagicMock(spec=ClaudeRunner)
        runner.run = AsyncMock()
        return runner

    @pytest.fixture
    def registry(self):
        reg = MagicMock(spec=AgentRegistry)
        reg.get = MagicMock(side_effect=lambda name: _make_agent(name))
        return reg

    @pytest.fixture
    def doc_store(self):
        store = MagicMock(spec=DocumentStore)
        store.read = MagicMock(side_effect=lambda name: f"content of {name}")
        return store

    @pytest.fixture
    def prompt_renderer(self):
        renderer = MagicMock(spec=PromptRenderer)
        renderer.render_agent_prompt = MagicMock(return_value="rendered prompt")
        return renderer

    @pytest.fixture
    def project_dir(self, tmp_path):
        return tmp_path / "project"

    @pytest.mark.asyncio
    async def test_state_saved_after_backend_completes(
        self, checkpointing_runner, claude, state_manager,
    ):
        """State is saved after backend QA cycle completes."""
        claude.run.side_effect = [
            _make_claude_result("backend code", cost=0.20),
            _make_claude_result("APPROVED", cost=0.10),
            _make_claude_result("frontend code", cost=0.25),
            _make_claude_result("APPROVED", cost=0.10),
        ]
        sprint_state = SprintState(sprint_number=1, name="Sprint 1")

        await checkpointing_runner.run_sprint(
            sprint_number=1, sprint_scope="scope", sprint_state=sprint_state,
        )

        assert state_manager.save.call_count >= 2

    @pytest.mark.asyncio
    async def test_session_id_saved_on_sprint_state(
        self, checkpointing_runner, claude, state_manager,
    ):
        """Session IDs from QA cycles are saved to sprint state."""
        claude.run.side_effect = [
            ClaudeResult(text="backend code", session_id="be-sess", cost_usd=0.20, exit_code=0),
            _make_claude_result("APPROVED", cost=0.10),
            ClaudeResult(text="frontend code", session_id="fe-sess", cost_usd=0.25, exit_code=0),
            _make_claude_result("APPROVED", cost=0.10),
        ]
        sprint_state = SprintState(sprint_number=1, name="Sprint 1")

        await checkpointing_runner.run_sprint(
            sprint_number=1, sprint_scope="scope", sprint_state=sprint_state,
        )

        assert sprint_state.backend_session_id == "be-sess"
        assert sprint_state.frontend_session_id == "fe-sess"

    @pytest.mark.asyncio
    async def test_skips_backend_when_status_is_frontend_dev(
        self, checkpointing_runner, claude, state_manager,
    ):
        """When sprint status is FRONTEND_DEV, backend QA cycle is skipped."""
        claude.run.side_effect = [
            _make_claude_result("frontend code", cost=0.25),
            _make_claude_result("APPROVED", cost=0.10),
        ]
        sprint_state = SprintState(
            sprint_number=1, name="Sprint 1",
            status=SprintStatus.FRONTEND_DEV,
        )

        result = await checkpointing_runner.run_sprint(
            sprint_number=1, sprint_scope="scope", sprint_state=sprint_state,
        )

        assert result.success is True
        assert result.backend_result is None
        assert result.frontend_result is not None
        assert claude.run.call_count == 2


class TestIntegrationMCPConfig:
    """Tests for MCP config resolution and passing to integration agent."""

    @pytest.fixture
    def runner(self, claude, tmp_path: Path) -> SprintRunner:
        registry = MagicMock(spec=AgentRegistry)
        registry.get = MagicMock(side_effect=lambda name: _make_agent(name))
        doc_store = MagicMock(spec=DocumentStore)
        doc_store.read = MagicMock(return_value="content")
        doc_store.exists = MagicMock(return_value=False)
        prompt_renderer = MagicMock(spec=PromptRenderer)
        prompt_renderer.render_agent_prompt = MagicMock(return_value="prompt")
        return SprintRunner(
            claude=claude,
            registry=registry,
            doc_store=doc_store,
            prompt_renderer=prompt_renderer,
            project_dir=tmp_path,
            project_type="fullstack",
        )

    @patch("agentic_dev.orchestrator.sprint_runner.discover_mcp_servers")
    def test_resolve_mcp_config_always_returns_none(self, mock_discover, runner) -> None:
        """Subprocess inherits MCP servers — always returns None."""
        from agentic_dev.mcp.claude_settings import ClaudeMCPEnvironment, MCPServerEntry
        mock_discover.return_value = ClaudeMCPEnvironment(
            servers={"figma": MCPServerEntry(name="figma", transport="stdio", source="global")}
        )
        config = runner._resolve_integration_mcp_config(["figma"])
        assert config is None

    def test_resolve_mcp_config_empty_list(self, runner) -> None:
        """Empty services list returns None."""
        config = runner._resolve_integration_mcp_config([])
        assert config is None

    @patch("agentic_dev.orchestrator.sprint_runner.discover_mcp_servers")
    def test_resolve_mcp_config_logs_warning_for_missing(self, mock_discover, runner) -> None:
        """Logs warnings for services not found in Claude Code settings."""
        from agentic_dev.mcp.claude_settings import ClaudeMCPEnvironment
        mock_discover.return_value = ClaudeMCPEnvironment(servers={})
        config = runner._resolve_integration_mcp_config(["nonexistent"])
        assert config is None


class TestFigmaExtraContext:
    """Tests for passing figma_sources to frontend agents via extra_context."""

    @pytest.mark.asyncio
    @patch("agentic_dev.orchestrator.sprint_runner.run_qa_cycle", new_callable=AsyncMock)
    @patch("agentic_dev.orchestrator.sprint_runner.check_figma_mcp_available")
    async def test_figma_sources_passed_in_extra_context(
        self, mock_check_figma, mock_qa_cycle, frontend_only_runner, doc_store
    ):
        """When figma_sources doc exists and MCP available, extra_context includes figma_sources."""
        doc_store.exists.side_effect = lambda name: name.replace(".md", "") == "figma_sources"
        doc_store.read.side_effect = lambda name: {
            "frontend_spec": "# Frontend Spec",
            "api_contract": "",
            "figma_sources": "# Figma Sources\n- URL: https://figma.com/file/abc",
        }.get(name.replace(".md", ""), "")

        mock_qa_cycle.return_value = MagicMock(
            total_cost=0.1, output="frontend output", session_id="s1",
        )

        await frontend_only_runner.run_sprint(1, "Build UI")

        # The frontend QA cycle call should have figma_sources in input_docs
        call_kwargs = mock_qa_cycle.call_args.kwargs
        assert "figma_sources" in call_kwargs["input_docs"]
        assert "figma.com/file/abc" in call_kwargs["input_docs"]["figma_sources"]
        assert call_kwargs["input_docs"]["figma_mcp_available"] == "true"

    @pytest.mark.asyncio
    @patch("agentic_dev.orchestrator.sprint_runner.run_qa_cycle", new_callable=AsyncMock)
    @patch("agentic_dev.orchestrator.sprint_runner.check_figma_mcp_available")
    async def test_figma_mcp_unavailable_sets_false(
        self, mock_check_figma, mock_qa_cycle, frontend_only_runner, doc_store
    ):
        """When figma_sources exists but MCP unavailable, figma_mcp_available is 'false'."""
        from agentic_dev.onboarding.figma import FigmaMCPNotConfigured

        doc_store.exists.side_effect = lambda name: name.replace(".md", "") == "figma_sources"
        doc_store.read.side_effect = lambda name: {
            "frontend_spec": "# Frontend Spec",
            "api_contract": "",
            "figma_sources": "# Figma Sources\n- URL: https://figma.com/file/abc",
        }.get(name.replace(".md", ""), "")

        mock_check_figma.side_effect = FigmaMCPNotConfigured()
        mock_qa_cycle.return_value = MagicMock(
            total_cost=0.1, output="frontend output", session_id="s1",
        )

        await frontend_only_runner.run_sprint(1, "Build UI")

        call_kwargs = mock_qa_cycle.call_args.kwargs
        assert call_kwargs["input_docs"]["figma_mcp_available"] == "false"

    @pytest.mark.asyncio
    @patch("agentic_dev.orchestrator.sprint_runner.run_qa_cycle", new_callable=AsyncMock)
    async def test_no_figma_sources_no_extra_context(
        self, mock_qa_cycle, frontend_only_runner, doc_store
    ):
        """When figma_sources doc does not exist, no figma keys in extra_context."""
        doc_store.exists.return_value = False
        doc_store.read.side_effect = lambda name: {
            "frontend_spec": "# Frontend Spec",
            "api_contract": "",
        }.get(name.replace(".md", ""), "")

        mock_qa_cycle.return_value = MagicMock(
            total_cost=0.1, output="frontend output", session_id="s1",
        )

        await frontend_only_runner.run_sprint(1, "Build UI")


class TestFrontendKindExtraContext:
    """frontend_kind from pipeline_state threads into frontend dev/QA extra_context."""

    @pytest.mark.asyncio
    @patch("agentic_dev.orchestrator.sprint_runner.run_qa_cycle", new_callable=AsyncMock)
    async def test_frontend_kind_passed_when_state_has_it(
        self, mock_qa_cycle, claude, registry, doc_store, prompt_renderer, project_dir
    ):
        from agentic_dev.orchestrator.sprint_runner import SprintRunner
        from agentic_dev.state.models import FrontendKind, PipelineState

        state = PipelineState(project_name="p", frontend_kind=FrontendKind.CLI)
        runner = SprintRunner(
            claude=claude,
            registry=registry,
            doc_store=doc_store,
            prompt_renderer=prompt_renderer,
            project_dir=project_dir,
            project_type="frontend_only",
            pipeline_state=state,
        )

        doc_store.exists.return_value = False
        doc_store.read.side_effect = lambda name: {
            "frontend_spec": "# Frontend Spec",
            "api_contract": "",
        }.get(name.replace(".md", ""), "")

        mock_qa_cycle.return_value = MagicMock(
            total_cost=0.1, output="frontend output", session_id="s1",
        )

        await runner.run_sprint(1, "Build CLI")

        call_kwargs = mock_qa_cycle.call_args.kwargs
        assert call_kwargs["input_docs"]["frontend_kind"] == "cli"

    @pytest.mark.asyncio
    @patch("agentic_dev.orchestrator.sprint_runner.run_qa_cycle", new_callable=AsyncMock)
    async def test_frontend_kind_absent_when_no_state(
        self, mock_qa_cycle, frontend_only_runner, doc_store
    ):
        """Without pipeline_state, extra_context has no frontend_kind key."""
        doc_store.exists.return_value = False
        doc_store.read.side_effect = lambda name: {
            "frontend_spec": "# Frontend Spec",
            "api_contract": "",
        }.get(name.replace(".md", ""), "")

        mock_qa_cycle.return_value = MagicMock(
            total_cost=0.1, output="frontend output", session_id="s1",
        )

        await frontend_only_runner.run_sprint(1, "Build UI")

        call_kwargs = mock_qa_cycle.call_args.kwargs
        assert "frontend_kind" not in call_kwargs["input_docs"]

        call_kwargs = mock_qa_cycle.call_args.kwargs
        assert "figma_sources" not in call_kwargs["input_docs"]
        assert "figma_mcp_available" not in call_kwargs["input_docs"]


class TestCrossSprintSummaries:
    """Tests for prior sprint summary collection and forwarding."""

    @pytest.fixture
    def runner_with_docs(self, claude, registry, prompt_renderer, project_dir):
        """Create a runner with a doc_store that has prior sprint docs."""
        store = MagicMock(spec=DocumentStore)

        docs = {
            "backend_spec": "# Backend Spec",
            "api_contract": "# API Contract",
            "sprint_rolling_summary": (
                "## Prior Sprint Summaries\n\n"
                "### Sprint 1 (backend)\n"
                "Created User model\n5 tests passing"
            ),
        }

        def mock_exists(name):
            return name in docs

        def mock_read(name):
            return docs.get(name, "")

        store.exists = MagicMock(side_effect=mock_exists)
        store.read = MagicMock(side_effect=mock_read)
        store.write = MagicMock()

        return SprintRunner(
            claude=claude,
            registry=registry,
            doc_store=store,
            prompt_renderer=prompt_renderer,
            project_dir=project_dir,
            project_type="backend_only",
        )

    @pytest.mark.asyncio
    @patch("agentic_dev.orchestrator.sprint_runner.run_qa_cycle")
    async def test_sprint_2_receives_sprint_1_summary(
        self, mock_qa_cycle, runner_with_docs, claude,
    ):
        mock_qa_cycle.return_value = MagicMock(
            total_cost=0.1, output="backend output", session_id="s1",
        )

        await runner_with_docs.run_sprint(2, "Build Payments")

        call_kwargs = mock_qa_cycle.call_args.kwargs
        input_docs = call_kwargs["input_docs"]
        assert "prior_sprint_summaries" in input_docs
        assert "Sprint 1 (backend)" in input_docs["prior_sprint_summaries"]
        assert "User model" in input_docs["prior_sprint_summaries"]

    @pytest.mark.asyncio
    @patch("agentic_dev.orchestrator.sprint_runner.run_qa_cycle")
    async def test_sprint_1_has_no_prior_summaries(
        self, mock_qa_cycle, claude, registry, prompt_renderer, project_dir,
    ):
        """Sprint 1 has no rolling summary document yet."""
        mock_qa_cycle.return_value = MagicMock(
            total_cost=0.1, output="backend output", session_id="s1",
        )

        store = MagicMock(spec=DocumentStore)
        docs = {
            "backend_spec": "# Backend Spec",
            "api_contract": "# API Contract",
        }
        store.exists = MagicMock(side_effect=lambda name: name in docs)
        store.read = MagicMock(side_effect=lambda name: docs.get(name, ""))
        store.write = MagicMock()

        runner = SprintRunner(
            claude=claude,
            registry=registry,
            doc_store=store,
            prompt_renderer=prompt_renderer,
            project_dir=project_dir,
            project_type="backend_only",
        )

        await runner.run_sprint(1, "Build Auth")

        call_kwargs = mock_qa_cycle.call_args.kwargs
        input_docs = call_kwargs["input_docs"]
        assert "prior_sprint_summaries" not in input_docs


# ---------------------------------------------------------------------------
# Rolling summary (_update_rolling_summary) (R6)
# ---------------------------------------------------------------------------


class TestRollingSummary:
    """Tests for the _update_rolling_summary method."""

    @pytest.fixture
    def runner_for_summary(
        self, claude, registry, prompt_renderer, project_dir,
    ):
        store = MagicMock(spec=DocumentStore)
        docs: dict[str, str] = {
            "backend_spec": "# Backend Spec",
            "api_contract": "# API Contract",
            "sprint_1_backend": "Line 1\nLine 2\nLine 3\nLine 4\nLine 5",
            "sprint_1_frontend": "FE line 1\nFE line 2\nFE line 3",
        }

        def mock_exists(name):
            return name in docs

        def mock_read(name):
            return docs.get(name, "")

        def mock_write(name, content):
            docs[name] = content

        store.exists = MagicMock(side_effect=mock_exists)
        store.read = MagicMock(side_effect=mock_read)
        store.write = MagicMock(side_effect=mock_write)
        store._docs = docs

        runner = SprintRunner(
            claude=claude,
            registry=registry,
            doc_store=store,
            prompt_renderer=prompt_renderer,
            project_dir=project_dir,
            project_type="fullstack",
        )
        return runner, store, docs

    def test_creates_rolling_summary_on_first_sprint(self, runner_for_summary):
        runner, store, docs = runner_for_summary
        runner._update_rolling_summary(1)

        store.write.assert_called_once()
        name, content = store.write.call_args[0]
        assert name == "sprint_rolling_summary"
        assert "## Prior Sprint Summaries" in content
        assert "### Sprint 1 (backend)" in content
        assert "### Sprint 1 (frontend)" in content

    def test_appends_to_existing_rolling_summary(self, runner_for_summary):
        runner, store, docs = runner_for_summary
        docs["sprint_rolling_summary"] = (
            "## Prior Sprint Summaries\n\n### Sprint 1 (backend)\nOld content"
        )
        docs["sprint_2_backend"] = "Sprint 2 backend work\nDone"

        runner._update_rolling_summary(2)

        write_calls = [c for c in store.write.call_args_list
                       if c[0][0] == "sprint_rolling_summary"]
        assert len(write_calls) == 1
        content = write_calls[0][0][1]
        assert "### Sprint 1 (backend)" in content
        assert "### Sprint 2 (backend)" in content

    def test_truncates_long_output_to_summary_lines(self, runner_for_summary):
        runner, store, docs = runner_for_summary
        long_output = "\n".join(f"Line {i}" for i in range(50))
        docs["sprint_1_backend"] = long_output

        runner._update_rolling_summary(1)

        name, content = store.write.call_args[0]
        backend_section = content.split("### Sprint 1 (backend)\n")[1]
        if "### Sprint 1 (frontend)" in backend_section:
            backend_section = backend_section.split("### Sprint 1 (frontend)")[0]
        lines = [l for l in backend_section.strip().splitlines() if l.strip()]
        assert len(lines) == runner._SUMMARY_LINES_PER_SPRINT

    def test_skips_missing_subphases(self, runner_for_summary):
        runner, store, docs = runner_for_summary
        # Remove frontend doc, keep only backend
        del docs["sprint_1_frontend"]

        runner._update_rolling_summary(1)

        name, content = store.write.call_args[0]
        assert "### Sprint 1 (backend)" in content
        assert "### Sprint 1 (frontend)" not in content

    def test_no_write_when_no_docs_exist(self, runner_for_summary):
        runner, store, docs = runner_for_summary
        # Sprint 99 has no docs
        runner._update_rolling_summary(99)
        store.write.assert_not_called()

    def test_includes_integration_subphase(self, runner_for_summary):
        runner, store, docs = runner_for_summary
        docs["sprint_1_integration"] = "Integration test results\nAll passing"

        runner._update_rolling_summary(1)

        name, content = store.write.call_args[0]
        assert "### Sprint 1 (integration)" in content
        assert "Integration test results" in content


# ---------------------------------------------------------------------------
# Integration QA receives its action output (regression for Bug 2)
# ---------------------------------------------------------------------------


class TestIntegrationQAReceivesActionOutput:
    """Regression: integration QA template needs the Integration Guide text.

    Unlike backend/frontend QA (which re-read code from disk), integration QA
    reviews a markdown artifact directly via ``{{ integration_guide }}``. The
    ``skip_action_output_in_qa`` optimisation must NOT apply to integration.
    """

    @pytest.mark.asyncio
    @patch(
        "agentic_dev.orchestrator.sprint_runner.run_qa_cycle",
        new_callable=AsyncMock,
    )
    async def test_integration_qa_does_not_skip_action_output(
        self, mock_qa_cycle, runner, claude,
    ):
        """The integration QA cycle must receive the action's output text."""
        mock_qa_cycle.return_value = MagicMock(
            total_cost=0.1, output="guide text", session_id="s1",
        )

        await runner.run_sprint(
            sprint_number=2,
            sprint_scope="payment",
            needs_integration=True,
        )

        # Three calls: backend, frontend, integration.
        assert mock_qa_cycle.call_count == 3
        integration_call = mock_qa_cycle.call_args_list[2]
        assert not integration_call.kwargs.get("skip_action_output_in_qa", False), (
            "Integration QA must pass the action output into the QA prompt; "
            "its template renders {{ integration_guide }} directly."
        )
        # Backend and frontend still use the optimisation.
        for idx in (0, 1):
            assert mock_qa_cycle.call_args_list[idx].kwargs.get(
                "skip_action_output_in_qa",
            ) is True

    def test_integration_qa_template_renders_with_integration_guide(self):
        """The integration_qa.md.j2 template must render given integration_guide.

        Positive scenario: when ``integration_guide`` IS present, the template
        renders successfully. Pairs with the
        ``test_integration_qa_template_crashes_without_integration_guide`` case.
        """
        from agentic_dev.config import PROMPT_TEMPLATES_DIR

        real_renderer = PromptRenderer(templates_dir=PROMPT_TEMPLATES_DIR)
        input_documents = {
            "api_contract": "# API Contract",
            "sprint_scope": "Sprint 2 scope",
            "integration_guide": "# Integration Guide\n## Service: AWS S3",
        }
        result = real_renderer.render_agent_prompt(
            template_name="integration_qa.md.j2",
            input_documents=input_documents,
            constraints=["No hardcoded credentials"],
        )
        assert "# Integration Guide" in result
        assert "AWS S3" in result
        assert "API Contract" in result

    def test_integration_qa_template_crashes_without_integration_guide(self):
        """Negative scenario: rendering the integration QA template without an
        ``integration_guide`` key raises ``TemplateRenderError``.

        This is exactly what the skillsbloom pipeline hit on resume. The test
        pins the contract so that any future change to the optimisation must
        either keep the key in QA inputs or update the template.
        """
        from agentic_dev.config import PROMPT_TEMPLATES_DIR
        from agentic_dev.prompts.renderer import TemplateRenderError

        real_renderer = PromptRenderer(templates_dir=PROMPT_TEMPLATES_DIR)
        input_documents = {
            "api_contract": "# API Contract",
            "sprint_scope": "Sprint 2 scope",
            # integration_guide omitted — this is the broken pipeline state.
        }
        with pytest.raises(TemplateRenderError, match="integration_guide"):
            real_renderer.render_agent_prompt(
                template_name="integration_qa.md.j2",
                input_documents=input_documents,
                constraints=["No hardcoded credentials"],
            )
