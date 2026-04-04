"""Tests for the pipeline engine."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_dev.agents.base import AgentDefinition, ClaudeConfig
from agentic_dev.agents.registry import AgentRegistry
from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import AgentRunError, CheckpointPause, OutputParseError
from agentic_dev.orchestrator.checkpoint import CheckpointConfig
from agentic_dev.orchestrator.engine import PipelineEngine
from agentic_dev.prompts.renderer import PromptRenderer
from agentic_dev.state.manager import StateManager
from agentic_dev.state.models import (
    PipelinePhase,
    PipelineState,
    ProjectType,
    SprintState,
    SprintStatus,
)


def _make_agent(name: str, template: str = "tpl.md.j2") -> AgentDefinition:
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


def _make_state(phase: PipelinePhase = PipelinePhase.IDLE, **kwargs) -> PipelineState:
    return PipelineState(project_name="test-project", phase=phase, **kwargs)


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
    store.read = MagicMock(return_value="document content")
    store.write = MagicMock()
    store.exists = MagicMock(return_value=False)
    return store


@pytest.fixture
def prompt_renderer() -> PromptRenderer:
    renderer = MagicMock(spec=PromptRenderer)
    renderer.render_agent_prompt = MagicMock(return_value="rendered prompt")
    return renderer


@pytest.fixture
def state_manager() -> StateManager:
    manager = MagicMock(spec=StateManager)
    manager.save = MagicMock()
    return manager


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path / "project"


@pytest.fixture
def engine(
    project_dir, claude, registry, doc_store, prompt_renderer, state_manager
) -> PipelineEngine:
    return PipelineEngine(
        project_dir=project_dir,
        claude=claude,
        registry=registry,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        state_manager=state_manager,
        checkpoint_config=CheckpointConfig(after_design=False),
    )


class TestRunAdvancesThroughPhases:
    """Test that run() progresses from IDLE toward COMPLETE."""

    @pytest.mark.asyncio
    async def test_input_processing_advances_to_feature_analysis(
        self, engine, state_manager, claude
    ):
        """Starting from IDLE, input processing runs and advances."""
        state = _make_state(PipelinePhase.IDLE)
        # After INPUT_PROCESSING advances to FEATURE_ANALYSIS, we stop
        # by making the next phase fail to load docs (to avoid running everything)
        call_count = 0

        def load_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return state
            # Return state as it is after modifications
            return state

        state_manager.load = MagicMock(side_effect=load_side_effect)
        claude.run.return_value = _make_claude_result("processed input", cost=0.05)

        # Make the feature analysis phase fail so we stop after input processing
        with patch.object(
            engine, "_run_feature_analysis", side_effect=AgentRunError("test", "stop")
        ):
            with pytest.raises(AgentRunError):
                await engine.run()

        # Verify input processing ran (claude was called)
        assert claude.run.called
        assert state.phase == PipelinePhase.FAILED


class TestCheckpointPause:
    """Test that checkpoints pause the pipeline."""

    @pytest.mark.asyncio
    async def test_design_checkpoint_raises_pause(
        self, project_dir, claude, registry, doc_store, prompt_renderer, state_manager
    ):
        """Pipeline pauses at DESIGN_CHECKPOINT when after_design is True."""
        config = CheckpointConfig(after_design=True)
        engine = PipelineEngine(
            project_dir=project_dir,
            claude=claude,
            registry=registry,
            doc_store=doc_store,
            prompt_renderer=prompt_renderer,
            state_manager=state_manager,
            checkpoint_config=config,
        )

        state = _make_state(PipelinePhase.SPRINT_PLANNING_QA)
        state_manager.load = MagicMock(return_value=state)

        with pytest.raises(CheckpointPause) as exc_info:
            await engine.run()

        assert exc_info.value.phase == PipelinePhase.DESIGN_CHECKPOINT
        state_manager.save.assert_called()

    @pytest.mark.asyncio
    async def test_no_pause_when_checkpoint_disabled(
        self, engine, state_manager, claude, project_dir
    ):
        """Pipeline does not pause at DESIGN_CHECKPOINT when after_design is False."""
        # engine fixture has after_design=False
        (project_dir / "frontend").mkdir(parents=True)
        (project_dir / "backend").mkdir(parents=True)
        state = _make_state(
            PipelinePhase.DESIGN_CHECKPOINT,
            sprints=[SprintState(sprint_number=1, name="Sprint 1")],
            current_sprint=1,
        )
        state_manager.load = MagicMock(return_value=state)

        # Will advance past checkpoint to SPRINTING, then we need sprint runner to work
        # Make sprint scope exist and run
        doc_store = engine._doc_store
        doc_store.exists = MagicMock(return_value=False)
        doc_store.read = MagicMock(return_value="content")

        # backend + QA, frontend + QA for sprint 1, then UAT
        claude.run.side_effect = [
            _make_claude_result("backend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            _make_claude_result("frontend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            _make_claude_result("uat report", cost=0.10),
        ]

        with patch(
            "agentic_dev.orchestrator.engine.init_repo", new_callable=AsyncMock
        ), patch(
            "agentic_dev.orchestrator.engine.commit", new_callable=AsyncMock
        ), patch(
            "agentic_dev.orchestrator.engine.write_claude_md"
        ), patch(
            "agentic_dev.orchestrator.engine.has_changes",
            new_callable=AsyncMock,
            return_value=False,
        ):
            await engine.run()

        assert state.phase == PipelinePhase.COMPLETE


class TestErrorHandling:
    """Test that errors set the FAILED state."""

    @pytest.mark.asyncio
    async def test_agent_run_error_sets_failed(self, engine, state_manager, claude):
        """AgentRunError during a phase sets state to FAILED."""
        state = _make_state(PipelinePhase.IDLE)
        state_manager.load = MagicMock(return_value=state)
        claude.run.side_effect = AgentRunError("input_processor", "boom")

        with pytest.raises(AgentRunError):
            await engine.run()

        assert state.phase == PipelinePhase.FAILED
        assert "boom" in state.error
        state_manager.save.assert_called()

    @pytest.mark.asyncio
    async def test_output_parse_error_sets_failed(self, engine, state_manager, claude):
        """OutputParseError during a phase sets state to FAILED."""
        state = _make_state(PipelinePhase.ARCHITECTURE)
        state_manager.load = MagicMock(return_value=state)

        # Architecture phase calls run_qa_cycle then split_documents
        # Make QA cycle succeed but split_documents fail
        claude.run.side_effect = [
            _make_claude_result("no markers here", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
        ]

        with pytest.raises(OutputParseError):
            await engine.run()

        assert state.phase == PipelinePhase.FAILED
        state_manager.save.assert_called()


class TestInputProcessingPhase:
    """Test the input processing phase in detail."""

    @pytest.mark.asyncio
    async def test_input_processing_calls_single_agent(
        self, engine, state_manager, claude, doc_store
    ):
        """Input processing uses _run_single_agent with docs/user_input content."""
        state = _make_state(PipelinePhase.INPUT_PROCESSING)
        state_manager.load = MagicMock(return_value=state)
        claude.run.return_value = _make_claude_result("structured output", cost=0.05)

        # Stop after input processing by failing feature analysis
        with patch.object(
            engine, "_run_feature_analysis", side_effect=AgentRunError("test", "stop")
        ):
            with pytest.raises(AgentRunError):
                await engine.run()

        doc_store.read.assert_any_call("user_input")
        doc_store.write.assert_any_call("structured_input", "structured output")

    @pytest.mark.asyncio
    async def test_input_processing_passes_user_input_to_renderer(
        self, engine, state_manager, claude, doc_store, prompt_renderer
    ):
        """input_processor template expects user_input; context key must match."""
        state = _make_state(PipelinePhase.INPUT_PROCESSING)
        state_manager.load = MagicMock(return_value=state)
        claude.run.return_value = _make_claude_result("structured output", cost=0.05)
        doc_store.read.return_value = "saved requirements body"

        with patch.object(
            engine, "_run_feature_analysis", side_effect=AgentRunError("test", "stop")
        ):
            with pytest.raises(AgentRunError):
                await engine.run()

        assert prompt_renderer.render_agent_prompt.call_count >= 1
        first_call = prompt_renderer.render_agent_prompt.call_args_list[0]
        input_docs = first_call.kwargs["input_documents"]
        assert input_docs == {"user_input": "saved requirements body"}
        assert "raw_input" not in input_docs


class TestFeatureAnalysisPhase:
    """Test the feature analysis phase with QA cycle."""

    @pytest.mark.asyncio
    async def test_feature_analysis_runs_qa_cycle(
        self, engine, state_manager, claude, doc_store
    ):
        """Feature analysis runs a full QA cycle and tracks cost."""
        state = _make_state(PipelinePhase.FEATURE_ANALYSIS)
        state_manager.load = MagicMock(return_value=state)

        # Feature analysis: action + QA (no issues)
        # Then FEATURE_ANALYSIS_QA advances to ARCHITECTURE which will fail
        claude.run.side_effect = [
            _make_claude_result("features output", cost=0.20),
            _make_claude_result("APPROVED", cost=0.15),
            # Architecture phase - will fail on split_documents
            _make_claude_result("arch output", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
        ]

        with pytest.raises(OutputParseError):
            await engine.run()

        # Verify cost was tracked from the feature analysis phase
        assert state.total_cost_usd >= 0.35
        assert len(state.agent_runs) >= 1
        assert state.agent_runs[0].agent_name == "feature_analyst"


class TestSprintPhase:
    """Test the sprinting phase."""

    @pytest.mark.asyncio
    async def test_sprints_run_sequentially(
        self, engine, state_manager, claude, project_dir
    ):
        """Each sprint runs in order and updates state."""
        (project_dir / "frontend").mkdir(parents=True)
        (project_dir / "backend").mkdir(parents=True)
        state = _make_state(
            PipelinePhase.SPRINTING,
            sprints=[
                SprintState(sprint_number=1, name="Sprint 1"),
                SprintState(sprint_number=2, name="Sprint 2"),
            ],
            current_sprint=1,
        )
        state_manager.load = MagicMock(return_value=state)
        doc_store = engine._doc_store
        doc_store.exists = MagicMock(return_value=False)
        doc_store.read = MagicMock(return_value="content")

        claude.run.side_effect = [
            # Sprint 1: backend + QA, frontend + QA
            _make_claude_result("s1 backend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            _make_claude_result("s1 frontend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            # Sprint 2: backend + QA, frontend + QA
            _make_claude_result("s2 backend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            _make_claude_result("s2 frontend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            # UAT
            _make_claude_result("uat report", cost=0.10),
        ]

        with patch(
            "agentic_dev.orchestrator.engine.has_changes",
            new_callable=AsyncMock,
            return_value=False,
        ), patch(
            "agentic_dev.orchestrator.engine.commit", new_callable=AsyncMock
        ):
            await engine.run()

        assert state.phase == PipelinePhase.COMPLETE
        assert state.sprints[0].status == SprintStatus.COMPLETE
        assert state.sprints[1].status == SprintStatus.COMPLETE
        assert state.total_cost_usd > 0


class TestUATPhase:
    """Test the UAT phase."""

    @pytest.mark.asyncio
    async def test_uat_runs_single_agent(self, engine, state_manager, claude, doc_store):
        """UAT runs without QA cycle and saves report."""
        state = _make_state(PipelinePhase.UAT)
        state_manager.load = MagicMock(return_value=state)
        doc_store.exists = MagicMock(return_value=True)
        doc_store.read = MagicMock(return_value="spec content")
        claude.run.return_value = _make_claude_result("uat passed", cost=0.20)

        await engine.run()

        assert state.phase == PipelinePhase.COMPLETE
        doc_store.write.assert_any_call("uat_report", "uat passed")


class TestSingleAgentRetry:
    """Test the empty-output retry in _run_single_agent (used by UAT and input processing)."""

    @pytest.mark.asyncio
    async def test_single_agent_retries_empty_output_then_succeeds(
        self, engine, state_manager, claude, doc_store
    ):
        """When _run_single_agent gets empty output once, it retries and succeeds."""
        state = _make_state(PipelinePhase.UAT)
        state_manager.load = MagicMock(return_value=state)
        doc_store.exists = MagicMock(return_value=True)
        doc_store.read = MagicMock(return_value="spec content")
        claude.run.side_effect = [
            _make_claude_result("", cost=0.01),       # empty — triggers retry
            _make_claude_result("uat passed", cost=0.20),  # retry succeeds
        ]

        with patch("agentic_dev.orchestrator.engine.asyncio.sleep"):
            await engine.run()

        assert state.phase == PipelinePhase.COMPLETE
        assert claude.run.call_count == 2

    @pytest.mark.asyncio
    async def test_single_agent_raises_after_retry_exhausted(
        self, engine, state_manager, claude, doc_store
    ):
        """When both the initial and retry calls return empty, AgentRunError is raised."""
        state = _make_state(PipelinePhase.UAT)
        state_manager.load = MagicMock(return_value=state)
        doc_store.exists = MagicMock(return_value=True)
        doc_store.read = MagicMock(return_value="spec content")
        claude.run.side_effect = [
            _make_claude_result("", cost=0.01),
            _make_claude_result("", cost=0.01),  # retry also empty
        ]

        with patch("agentic_dev.orchestrator.engine.asyncio.sleep"):
            with pytest.raises(AgentRunError, match="empty output"):
                await engine.run()

        assert claude.run.call_count == 2


class TestProjectTypeDetection:
    """Test that the engine parses project type from structured_input."""

    @pytest.mark.asyncio
    async def test_input_processing_parses_project_type(
        self, engine, state_manager, claude, doc_store
    ):
        """Project type is parsed from structured_input after input processing."""
        state = _make_state(PipelinePhase.INPUT_PROCESSING)
        state_manager.load = MagicMock(return_value=state)

        structured_output = (
            "# Structured Input\n"
            "## Project Type\nfrontend_only\n"
            "## Feature Requirements\n- Build a React app"
        )
        claude.run.return_value = _make_claude_result(structured_output, cost=0.05)

        with patch.object(
            engine, "_run_feature_analysis", side_effect=AgentRunError("test", "stop")
        ):
            with pytest.raises(AgentRunError):
                await engine.run()

        assert state.project_type == ProjectType.FRONTEND_ONLY

    @pytest.mark.asyncio
    async def test_input_processing_defaults_to_fullstack(
        self, engine, state_manager, claude, doc_store
    ):
        """When no project type is found in structured_input, default to fullstack."""
        state = _make_state(PipelinePhase.INPUT_PROCESSING)
        state_manager.load = MagicMock(return_value=state)

        structured_output = "# Structured Input\n## Feature Requirements\n- Build an app"
        claude.run.return_value = _make_claude_result(structured_output, cost=0.05)

        with patch.object(
            engine, "_run_feature_analysis", side_effect=AgentRunError("test", "stop")
        ):
            with pytest.raises(AgentRunError):
                await engine.run()

        assert state.project_type == ProjectType.FULLSTACK

    @pytest.mark.asyncio
    async def test_architecture_uses_expected_docs_for_frontend_only(
        self, engine, state_manager, claude, doc_store
    ):
        """Architecture phase uses state.expected_architecture_docs for splitting."""
        state = _make_state(
            PipelinePhase.ARCHITECTURE,
            project_type=ProjectType.FRONTEND_ONLY,
        )
        state_manager.load = MagicMock(return_value=state)

        arch_output = "<!-- DOCUMENT: frontend_spec -->\n# Frontend Spec\nContent here"
        claude.run.side_effect = [
            _make_claude_result(arch_output, cost=0.20),
            _make_claude_result("APPROVED", cost=0.10),
        ]

        # Should succeed with just frontend_spec (not expecting 3 docs)
        with patch.object(
            engine, "_run_sprint_planning", side_effect=AgentRunError("test", "stop")
        ):
            # It will advance through ARCHITECTURE_QA to SPRINT_PLANNING, then fail
            with pytest.raises(AgentRunError):
                await engine.run()

        doc_store.write.assert_any_call("frontend_spec", "# Frontend Spec\nContent here")

    @pytest.mark.asyncio
    async def test_architecture_passes_project_type_as_extra_context(
        self, engine, state_manager, claude, doc_store, prompt_renderer
    ):
        """Architecture QA cycle receives project_type via extra_context."""
        state = _make_state(
            PipelinePhase.ARCHITECTURE,
            project_type=ProjectType.BACKEND_ONLY,
        )
        state_manager.load = MagicMock(return_value=state)

        arch_output = (
            "<!-- DOCUMENT: backend_spec -->\n# Backend Spec\nModels\n"
            "<!-- DOCUMENT: api_contract -->\n# API Contract\nEndpoints"
        )
        claude.run.side_effect = [
            _make_claude_result(arch_output, cost=0.20),
            _make_claude_result("APPROVED", cost=0.10),
        ]

        with patch.object(
            engine, "_run_sprint_planning", side_effect=AgentRunError("test", "stop")
        ):
            with pytest.raises(AgentRunError):
                await engine.run()

        # Verify extra_context was passed to run_qa_cycle
        render_calls = prompt_renderer.render_agent_prompt.call_args_list
        any_has_project_type = any(
            call.kwargs.get("extra_context", {}).get("project_type") == "backend_only"
            for call in render_calls
        )
        assert any_has_project_type

    @pytest.mark.asyncio
    async def test_architecture_passes_design_analyses_when_exists(
        self, engine, state_manager, claude, doc_store, prompt_renderer
    ):
        """When design_analyses exists, it is included in architect input_docs."""
        state = _make_state(PipelinePhase.ARCHITECTURE)
        state_manager.load = MagicMock(return_value=state)

        def exists_side_effect(name):
            return name == "design_analyses"

        doc_store.exists = MagicMock(side_effect=exists_side_effect)

        def read_side_effect(name):
            if name == "design_analyses":
                return "## Design Tokens\nColors: blue-500"
            return "document content"

        doc_store.read = MagicMock(side_effect=read_side_effect)

        arch_output = (
            "<!-- DOCUMENT: frontend_spec -->\n# Frontend Spec\nContent\n"
            "<!-- DOCUMENT: backend_spec -->\n# Backend Spec\nContent\n"
            "<!-- DOCUMENT: api_contract -->\n# API Contract\nContent"
        )
        claude.run.side_effect = [
            _make_claude_result(arch_output, cost=0.20),
            _make_claude_result("APPROVED", cost=0.10),
        ]

        with patch.object(
            engine, "_run_sprint_planning", side_effect=AgentRunError("test", "stop")
        ):
            with pytest.raises(AgentRunError):
                await engine.run()

        render_calls = prompt_renderer.render_agent_prompt.call_args_list
        arch_call = render_calls[0]
        input_docs = arch_call.kwargs["input_documents"]
        assert "design_analyses" in input_docs
        assert "blue-500" in input_docs["design_analyses"]

    @pytest.mark.asyncio
    async def test_architecture_passes_empty_design_analyses_when_absent(
        self, engine, state_manager, claude, doc_store, prompt_renderer
    ):
        """When design_analyses does not exist, empty string is passed."""
        state = _make_state(PipelinePhase.ARCHITECTURE)
        state_manager.load = MagicMock(return_value=state)

        doc_store.exists = MagicMock(return_value=False)

        arch_output = (
            "<!-- DOCUMENT: frontend_spec -->\n# Frontend Spec\nContent\n"
            "<!-- DOCUMENT: backend_spec -->\n# Backend Spec\nContent\n"
            "<!-- DOCUMENT: api_contract -->\n# API Contract\nContent"
        )
        claude.run.side_effect = [
            _make_claude_result(arch_output, cost=0.20),
            _make_claude_result("APPROVED", cost=0.10),
        ]

        with patch.object(
            engine, "_run_sprint_planning", side_effect=AgentRunError("test", "stop")
        ):
            with pytest.raises(AgentRunError):
                await engine.run()

        render_calls = prompt_renderer.render_agent_prompt.call_args_list
        arch_call = render_calls[0]
        input_docs = arch_call.kwargs["input_documents"]
        assert "design_analyses" in input_docs
        assert input_docs["design_analyses"] == ""

    @pytest.mark.asyncio
    async def test_sprint_planning_reads_only_available_docs(
        self, engine, state_manager, claude, doc_store
    ):
        """For frontend_only, sprint planning reads frontend_spec but not backend_spec."""
        state = _make_state(
            PipelinePhase.SPRINT_PLANNING,
            project_type=ProjectType.FRONTEND_ONLY,
        )
        state_manager.load = MagicMock(return_value=state)

        # Track which doc names are read
        read_docs = []

        def mock_read(name):
            read_docs.append(name)
            return f"content of {name}"

        doc_store.read = MagicMock(side_effect=mock_read)

        claude.run.side_effect = [
            _make_claude_result("# Sprint Plan\n## Sprint 1: UI", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
        ]

        with patch.object(
            engine, "_advance_past_checkpoint", side_effect=AgentRunError("test", "stop")
        ):
            with pytest.raises(AgentRunError):
                await engine.run()

        assert "frontend_spec" in read_docs
        assert "backend_spec" not in read_docs
        assert "api_contract" not in read_docs


class TestWorkspaceSetup:
    """Test that _advance_past_checkpoint sets up git repos and CLAUDE.md."""

    @pytest.mark.asyncio
    async def test_inits_git_repos_for_fullstack(
        self, engine, state_manager, project_dir
    ):
        """Git repos are initialized for both frontend and backend."""
        (project_dir / "frontend").mkdir(parents=True)
        (project_dir / "backend").mkdir(parents=True)
        state = _make_state(
            PipelinePhase.DESIGN_CHECKPOINT,
            project_type=ProjectType.FULLSTACK,
            sprints=[SprintState(sprint_number=1, name="Sprint 1")],
        )
        state_manager.load = MagicMock(return_value=state)

        with patch(
            "agentic_dev.orchestrator.engine.init_repo", new_callable=AsyncMock
        ) as mock_init, patch(
            "agentic_dev.orchestrator.engine.commit", new_callable=AsyncMock
        ), patch.object(
            engine, "_run_sprints", side_effect=AgentRunError("test", "stop")
        ):
            with pytest.raises(AgentRunError):
                await engine.run()

        init_paths = [c.args[0] for c in mock_init.call_args_list]
        assert project_dir / "frontend" in init_paths
        assert project_dir / "backend" in init_paths

    @pytest.mark.asyncio
    async def test_generates_claude_md(
        self, engine, state_manager, project_dir, doc_store
    ):
        """CLAUDE.md is written to each code directory."""
        (project_dir / "frontend").mkdir(parents=True)
        (project_dir / "backend").mkdir(parents=True)
        state = _make_state(
            PipelinePhase.DESIGN_CHECKPOINT,
            project_type=ProjectType.FULLSTACK,
            sprints=[SprintState(sprint_number=1, name="Sprint 1")],
        )
        state_manager.load = MagicMock(return_value=state)

        doc_store.read = MagicMock(return_value="## Tech Stack\n- **Framework:** Next.js")
        doc_store.exists = MagicMock(return_value=False)

        with patch(
            "agentic_dev.orchestrator.engine.init_repo", new_callable=AsyncMock
        ), patch(
            "agentic_dev.orchestrator.engine.commit", new_callable=AsyncMock
        ), patch(
            "agentic_dev.orchestrator.engine.write_claude_md"
        ) as mock_write, patch.object(
            engine, "_run_sprints", side_effect=AgentRunError("test", "stop")
        ):
            with pytest.raises(AgentRunError):
                await engine.run()

        write_dirs = [c.args[0] for c in mock_write.call_args_list]
        assert project_dir / "frontend" in write_dirs
        assert project_dir / "backend" in write_dirs

    @pytest.mark.asyncio
    async def test_makes_initial_commit(
        self, engine, state_manager, project_dir, doc_store
    ):
        """An initial commit is made in each repo after setup."""
        (project_dir / "frontend").mkdir(parents=True)
        (project_dir / "backend").mkdir(parents=True)
        state = _make_state(
            PipelinePhase.DESIGN_CHECKPOINT,
            project_type=ProjectType.FULLSTACK,
            sprints=[SprintState(sprint_number=1, name="Sprint 1")],
        )
        state_manager.load = MagicMock(return_value=state)

        with patch(
            "agentic_dev.orchestrator.engine.init_repo", new_callable=AsyncMock
        ), patch(
            "agentic_dev.orchestrator.engine.commit", new_callable=AsyncMock
        ) as mock_commit, patch(
            "agentic_dev.orchestrator.engine.write_claude_md"
        ), patch.object(
            engine, "_run_sprints", side_effect=AgentRunError("test", "stop")
        ):
            with pytest.raises(AgentRunError):
                await engine.run()

        commit_paths = [c.args[0] for c in mock_commit.call_args_list]
        assert project_dir / "frontend" in commit_paths
        assert project_dir / "backend" in commit_paths
        for c in mock_commit.call_args_list:
            assert "Initial commit" in c.args[1]

    @pytest.mark.asyncio
    async def test_frontend_only_skips_backend(
        self, engine, state_manager, project_dir
    ):
        """Only frontend is set up for frontend_only projects."""
        (project_dir / "frontend").mkdir(parents=True)
        state = _make_state(
            PipelinePhase.DESIGN_CHECKPOINT,
            project_type=ProjectType.FRONTEND_ONLY,
            sprints=[SprintState(sprint_number=1, name="Sprint 1")],
        )
        state_manager.load = MagicMock(return_value=state)

        with patch(
            "agentic_dev.orchestrator.engine.init_repo", new_callable=AsyncMock
        ) as mock_init, patch(
            "agentic_dev.orchestrator.engine.commit", new_callable=AsyncMock
        ), patch(
            "agentic_dev.orchestrator.engine.write_claude_md"
        ), patch.object(
            engine, "_run_sprints", side_effect=AgentRunError("test", "stop")
        ):
            with pytest.raises(AgentRunError):
                await engine.run()

        init_paths = [c.args[0] for c in mock_init.call_args_list]
        assert project_dir / "frontend" in init_paths
        assert project_dir / "backend" not in init_paths

    @pytest.mark.asyncio
    async def test_backend_only_skips_frontend(
        self, engine, state_manager, project_dir
    ):
        """Only backend is set up for backend_only projects."""
        (project_dir / "backend").mkdir(parents=True)
        state = _make_state(
            PipelinePhase.DESIGN_CHECKPOINT,
            project_type=ProjectType.BACKEND_ONLY,
            sprints=[SprintState(sprint_number=1, name="Sprint 1")],
        )
        state_manager.load = MagicMock(return_value=state)

        with patch(
            "agentic_dev.orchestrator.engine.init_repo", new_callable=AsyncMock
        ) as mock_init, patch(
            "agentic_dev.orchestrator.engine.commit", new_callable=AsyncMock
        ), patch(
            "agentic_dev.orchestrator.engine.write_claude_md"
        ), patch.object(
            engine, "_run_sprints", side_effect=AgentRunError("test", "stop")
        ):
            with pytest.raises(AgentRunError):
                await engine.run()

        init_paths = [c.args[0] for c in mock_init.call_args_list]
        assert project_dir / "frontend" not in init_paths
        assert project_dir / "backend" in init_paths

    @pytest.mark.asyncio
    async def test_handles_missing_spec_gracefully(
        self, engine, state_manager, project_dir, doc_store
    ):
        """Uses defaults when spec docs are unavailable."""
        (project_dir / "frontend").mkdir(parents=True)
        state = _make_state(
            PipelinePhase.DESIGN_CHECKPOINT,
            project_type=ProjectType.FRONTEND_ONLY,
            sprints=[SprintState(sprint_number=1, name="Sprint 1")],
        )
        state_manager.load = MagicMock(return_value=state)
        doc_store.exists = MagicMock(return_value=False)
        doc_store.read = MagicMock(side_effect=FileNotFoundError("not found"))

        with patch(
            "agentic_dev.orchestrator.engine.init_repo", new_callable=AsyncMock
        ), patch(
            "agentic_dev.orchestrator.engine.commit", new_callable=AsyncMock
        ), patch(
            "agentic_dev.orchestrator.engine.write_claude_md"
        ) as mock_write, patch.object(
            engine, "_run_sprints", side_effect=AgentRunError("test", "stop")
        ):
            with pytest.raises(AgentRunError):
                await engine.run()

        # Should still write CLAUDE.md with defaults
        assert mock_write.call_count == 1


class TestPostSprintCommits:
    """Test that git commits happen after successful sprints."""

    @pytest.mark.asyncio
    async def test_commits_after_successful_sprint(
        self, engine, state_manager, claude, project_dir
    ):
        """Git commit is called for frontend and backend after a successful sprint."""
        (project_dir / "frontend").mkdir(parents=True)
        (project_dir / "backend").mkdir(parents=True)
        state = _make_state(
            PipelinePhase.SPRINTING,
            sprints=[SprintState(sprint_number=1, name="Core Features")],
            current_sprint=1,
        )
        state_manager.load = MagicMock(return_value=state)
        doc_store = engine._doc_store
        doc_store.exists = MagicMock(return_value=False)
        doc_store.read = MagicMock(return_value="content")

        claude.run.side_effect = [
            _make_claude_result("backend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            _make_claude_result("frontend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            _make_claude_result("uat report", cost=0.10),
        ]

        with patch(
            "agentic_dev.orchestrator.engine.has_changes",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "agentic_dev.orchestrator.engine.commit", new_callable=AsyncMock
        ) as mock_commit:
            await engine.run()

        commit_paths = [c.args[0] for c in mock_commit.call_args_list]
        assert project_dir / "frontend" in commit_paths
        assert project_dir / "backend" in commit_paths

    @pytest.mark.asyncio
    async def test_commit_message_includes_sprint_info(
        self, engine, state_manager, claude, project_dir
    ):
        """Commit message references sprint number and name."""
        (project_dir / "frontend").mkdir(parents=True)
        (project_dir / "backend").mkdir(parents=True)
        state = _make_state(
            PipelinePhase.SPRINTING,
            sprints=[SprintState(sprint_number=1, name="Core Features")],
            current_sprint=1,
        )
        state_manager.load = MagicMock(return_value=state)
        doc_store = engine._doc_store
        doc_store.exists = MagicMock(return_value=False)
        doc_store.read = MagicMock(return_value="content")

        claude.run.side_effect = [
            _make_claude_result("backend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            _make_claude_result("frontend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            _make_claude_result("uat report", cost=0.10),
        ]

        with patch(
            "agentic_dev.orchestrator.engine.has_changes",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "agentic_dev.orchestrator.engine.commit", new_callable=AsyncMock
        ) as mock_commit:
            await engine.run()

        for c in mock_commit.call_args_list:
            assert "Sprint 1" in c.args[1]
            assert "Core Features" in c.args[1]

    @pytest.mark.asyncio
    async def test_failed_sprint_does_not_commit(
        self, engine, state_manager, claude, project_dir
    ):
        """No commit when sprint fails."""
        (project_dir / "frontend").mkdir(parents=True)
        (project_dir / "backend").mkdir(parents=True)
        state = _make_state(
            PipelinePhase.SPRINTING,
            sprints=[SprintState(sprint_number=1, name="Sprint 1")],
            current_sprint=1,
        )
        state_manager.load = MagicMock(return_value=state)
        doc_store = engine._doc_store
        doc_store.exists = MagicMock(return_value=False)
        doc_store.read = MagicMock(return_value="content")

        claude.run.side_effect = AgentRunError("backend_developer", "failed")

        with patch(
            "agentic_dev.orchestrator.engine.has_changes",
            new_callable=AsyncMock,
        ), patch(
            "agentic_dev.orchestrator.engine.commit", new_callable=AsyncMock
        ) as mock_commit:
            await engine.run()

        mock_commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_commit_when_no_changes(
        self, engine, state_manager, claude, project_dir
    ):
        """No commit attempted when has_changes returns False."""
        (project_dir / "frontend").mkdir(parents=True)
        (project_dir / "backend").mkdir(parents=True)
        state = _make_state(
            PipelinePhase.SPRINTING,
            sprints=[SprintState(sprint_number=1, name="Sprint 1")],
            current_sprint=1,
        )
        state_manager.load = MagicMock(return_value=state)
        doc_store = engine._doc_store
        doc_store.exists = MagicMock(return_value=False)
        doc_store.read = MagicMock(return_value="content")

        claude.run.side_effect = [
            _make_claude_result("backend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            _make_claude_result("frontend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            _make_claude_result("uat report", cost=0.10),
        ]

        with patch(
            "agentic_dev.orchestrator.engine.has_changes",
            new_callable=AsyncMock,
            return_value=False,
        ), patch(
            "agentic_dev.orchestrator.engine.commit", new_callable=AsyncMock
        ) as mock_commit:
            await engine.run()

        mock_commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_frontend_only_commits_only_frontend(
        self, engine, state_manager, claude, project_dir
    ):
        """Only frontend dir is committed for frontend_only projects."""
        (project_dir / "frontend").mkdir(parents=True)
        state = _make_state(
            PipelinePhase.SPRINTING,
            project_type=ProjectType.FRONTEND_ONLY,
            sprints=[SprintState(sprint_number=1, name="Sprint 1")],
            current_sprint=1,
        )
        state_manager.load = MagicMock(return_value=state)
        doc_store = engine._doc_store
        doc_store.exists = MagicMock(return_value=False)
        doc_store.read = MagicMock(return_value="content")

        claude.run.side_effect = [
            _make_claude_result("frontend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            _make_claude_result("uat report", cost=0.10),
        ]

        with patch(
            "agentic_dev.orchestrator.engine.has_changes",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "agentic_dev.orchestrator.engine.commit", new_callable=AsyncMock
        ) as mock_commit:
            await engine.run()

        commit_paths = [c.args[0] for c in mock_commit.call_args_list]
        assert project_dir / "frontend" in commit_paths
        assert project_dir / "backend" not in commit_paths
