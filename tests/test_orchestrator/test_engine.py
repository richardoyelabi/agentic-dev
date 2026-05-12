"""Tests for the pipeline engine."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_dev.agents.base import AgentDefinition, ClaudeConfig
from agentic_dev.agents.registry import AgentRegistry
from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import (
    AgentRunError,
    CheckpointPause,
    GracefulShutdown,
    OutputParseError,
    RateLimitError,
    RateLimitPause,
)
from agentic_dev.orchestrator.checkpoint import CheckpointConfig
from agentic_dev.orchestrator.engine import PipelineEngine
from agentic_dev.prompts.renderer import PromptRenderer
from agentic_dev.state.manager import StateManager
from agentic_dev.state.models import (
    PipelinePhase,
    PipelineState,
    SprintState,
    SprintStatus,
)
from agentic_dev.tracks import Track


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


_LEGACY_PROJECT_TYPE_TRACKS = {
    "fullstack": [
        Track(name="frontend", path="frontend", kind="web", uat_kind="web"),
        Track(name="backend", path="backend", kind="api", uat_kind="api"),
    ],
    "frontend_only": [
        Track(name="frontend", path="frontend", kind="web", uat_kind="web"),
    ],
    "backend_only": [
        Track(name="backend", path="backend", kind="api", uat_kind="api"),
    ],
}


def _make_state(phase: PipelinePhase = PipelinePhase.IDLE, **kwargs) -> PipelineState:
    """Helper that accepts legacy ``project_type``/``frontend_kind`` kwargs.

    Legacy ``project_type`` is translated to a default tracks list; legacy
    ``frontend_kind`` is silently dropped. When neither ``project_type`` nor
    ``tracks`` is supplied, a fullstack default (frontend + backend) is used
    to preserve the legacy implicit-fullstack semantics that older tests rely on.
    """
    legacy_project_type = kwargs.pop("project_type", None)
    kwargs.pop("frontend_kind", None)
    if "tracks" not in kwargs:
        key = "fullstack"
        if legacy_project_type is not None:
            key = getattr(legacy_project_type, "value", legacy_project_type)
        kwargs["tracks"] = list(_LEGACY_PROJECT_TYPE_TRACKS.get(key, []))
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
def doc_store(tmp_path: Path) -> DocumentStore:
    store = MagicMock(spec=DocumentStore)
    store.read = MagicMock(return_value="document content")
    store.write = MagicMock()
    store.exists = MagicMock(return_value=False)
    store.docs_dir = tmp_path / "docs"
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

        # backend + QA, frontend + QA for sprint 1, then UAT + QA
        claude.run.side_effect = [
            _make_claude_result("backend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            _make_claude_result("frontend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            _make_claude_result("uat backend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            _make_claude_result("uat frontend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
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


class TestRateLimitPauseHandling:
    """RateLimitError must pause the pipeline, not fail it."""

    @pytest.mark.asyncio
    async def test_rate_limit_error_raises_pause_preserving_phase(
        self, engine, state_manager, claude,
    ):
        """RateLimitError during a phase raises RateLimitPause without transitioning to FAILED."""
        state = _make_state(PipelinePhase.INPUT_PROCESSING)
        state_manager.load = MagicMock(return_value=state)
        claude.run.side_effect = RateLimitError(
            agent_name="input_processor",
            message="Rate limited after 6 attempts",
            attempts=6,
            exit_code=1,
        )

        with patch("agentic_dev.orchestrator.engine.UsageApiClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.get_utilization = AsyncMock(return_value=None)
            mock_cls.return_value = mock_instance

            with pytest.raises(RateLimitPause) as exc_info:
                await engine.run()

        # Phase preserved — NOT transitioned to FAILED
        assert state.phase == PipelinePhase.INPUT_PROCESSING
        assert state.failed_at_phase is None
        assert state.error is None
        # Pause carries the agent name
        assert exc_info.value.agent_name == "input_processor"
        # State was still saved so re-entry picks up the same phase
        state_manager.save.assert_called()

    @pytest.mark.asyncio
    async def test_rate_limit_error_during_sprint_propagates_as_pause(
        self, engine, state_manager, claude, project_dir,
    ):
        """A RateLimitError inside a sprint surfaces as RateLimitPause, not FAILED."""
        (project_dir / "frontend").mkdir(parents=True)
        (project_dir / "backend").mkdir(parents=True)
        sprint = SprintState(sprint_number=1, name="Sprint 1")
        state = _make_state(
            PipelinePhase.SPRINTING,
            sprints=[sprint],
            current_sprint=1,
        )
        state_manager.load = MagicMock(return_value=state)
        doc_store = engine._doc_store
        doc_store.exists = MagicMock(return_value=False)
        doc_store.read = MagicMock(return_value="content")

        claude.run.side_effect = RateLimitError(
            agent_name="backend_developer",
            message="Rate limited after 6 attempts",
            attempts=6,
        )

        with patch(
            "agentic_dev.orchestrator.engine.has_changes",
            new_callable=AsyncMock, return_value=False,
        ), patch(
            "agentic_dev.orchestrator.engine.commit", new_callable=AsyncMock,
        ), patch(
            "agentic_dev.orchestrator.engine.UsageApiClient",
        ) as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.get_utilization = AsyncMock(return_value=None)
            mock_cls.return_value = mock_instance

            with pytest.raises(RateLimitPause):
                await engine.run()

        # Sprint phase preserved — the sprint can resume on re-entry
        assert state.phase == PipelinePhase.SPRINTING
        assert state.failed_at_phase is None

    @pytest.mark.asyncio
    async def test_rate_limit_pause_fails_when_wait_exceeds_cap(
        self, engine, state_manager, claude,
    ):
        """When the computed wait exceeds the configured cap, fall through to FAILED."""
        from datetime import datetime, timedelta, timezone

        from agentic_dev.claude.rate_limiter import UsageStatus

        state = _make_state(PipelinePhase.INPUT_PROCESSING)
        state_manager.load = MagicMock(return_value=state)
        claude.run.side_effect = RateLimitError(
            agent_name="input_processor",
            message="Rate limited",
            attempts=6,
        )

        # Usage API says reset is in 10 hours — over the 6-hour cap
        long_reset = datetime.now(timezone.utc) + timedelta(hours=10)
        status = UsageStatus(five_hour=100.0, is_limited=True, resets_at=long_reset)

        with patch(
            "agentic_dev.orchestrator.engine.UsageApiClient"
        ) as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.get_utilization = AsyncMock(return_value=status)
            mock_cls.return_value = mock_instance

            with pytest.raises(AgentRunError):  # RateLimitError IS an AgentRunError
                await engine.run()

        assert state.phase == PipelinePhase.FAILED

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
    async def test_input_processing_runs_qa_cycle(
        self, engine, state_manager, claude, doc_store
    ):
        """Input processing uses run_qa_cycle with docs/user_input content."""
        state = _make_state(PipelinePhase.INPUT_PROCESSING)
        state_manager.load = MagicMock(return_value=state)
        claude.run.side_effect = [
            _make_claude_result("structured output", cost=0.05),
            _make_claude_result("APPROVED", cost=0.03),
        ]

        # Stop after input processing QA by failing feature analysis
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
        claude.run.side_effect = [
            _make_claude_result("structured output", cost=0.05),
            _make_claude_result("APPROVED", cost=0.03),
        ]
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
            # UAT + QA (two UAT-capable tracks for fullstack: backend api + frontend web)
            _make_claude_result("uat backend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            _make_claude_result("uat frontend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
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
    async def test_uat_runs_qa_cycle(self, engine, state_manager, claude, doc_store):
        """UAT runs once per UAT-capable track and aggregates the report."""
        state = _make_state(PipelinePhase.UAT)
        state_manager.load = MagicMock(return_value=state)
        doc_store.exists = MagicMock(return_value=True)
        doc_store.read = MagicMock(return_value="spec content")
        claude.run.side_effect = [
            _make_claude_result("uat backend\n## Overall Result: PASS", cost=0.20),
            _make_claude_result("APPROVED", cost=0.10),
            _make_claude_result("uat frontend\n## Overall Result: PASS", cost=0.20),
            _make_claude_result("APPROVED", cost=0.10),
        ]

        await engine.run()

        assert state.phase == PipelinePhase.COMPLETE
        # Aggregated report references each track
        write_calls = [c.args[0] for c in doc_store.write.call_args_list]
        assert "uat_report" in write_calls


class TestQACycleRetry:
    """Test the empty-output retry in QA cycle (used by UAT and input processing)."""

    @pytest.mark.asyncio
    async def test_qa_cycle_retries_empty_output_then_succeeds(
        self, engine, state_manager, claude, doc_store
    ):
        """When the action agent returns empty once, the QA cycle retries and succeeds."""
        state = _make_state(PipelinePhase.UAT)
        state_manager.load = MagicMock(return_value=state)
        doc_store.exists = MagicMock(return_value=True)
        doc_store.read = MagicMock(return_value="spec content")
        claude.run.side_effect = [
            _make_claude_result("", cost=0.01),       # backend UAT: empty — triggers retry
            _make_claude_result("uat passed\n## Overall Result: PASS", cost=0.20),  # retry succeeds
            _make_claude_result("APPROVED", cost=0.10),  # backend UAT QA
            _make_claude_result("uat passed\n## Overall Result: PASS", cost=0.20),  # frontend UAT action
            _make_claude_result("APPROVED", cost=0.10),  # frontend UAT QA
        ]

        with patch("agentic_dev.orchestrator.qa_cycle.asyncio.sleep"):
            await engine.run()

        assert state.phase == PipelinePhase.COMPLETE
        assert claude.run.call_count == 5

    @pytest.mark.asyncio
    async def test_qa_cycle_raises_after_retry_exhausted(
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

        with patch("agentic_dev.orchestrator.qa_cycle.asyncio.sleep"):
            with pytest.raises(AgentRunError, match="empty output"):
                await engine.run()

        assert claude.run.call_count == 2


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
            project_type="fullstack",
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
            project_type="fullstack",
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
            project_type="fullstack",
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
            project_type="frontend_only",
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
            project_type="backend_only",
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
            project_type="frontend_only",
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

    @pytest.mark.asyncio
    async def test_skips_workspace_setup_for_update_mode(
        self, engine, state_manager, project_dir
    ):
        """Workspace setup is skipped entirely for update mode."""
        (project_dir / "frontend").mkdir(parents=True)
        (project_dir / "backend").mkdir(parents=True)
        state = _make_state(
            PipelinePhase.DESIGN_CHECKPOINT,
            project_type="fullstack",
            mode="update",
            sprints=[SprintState(sprint_number=1, name="Sprint 1")],
        )
        state_manager.load = MagicMock(return_value=state)

        with patch(
            "agentic_dev.orchestrator.engine.init_repo", new_callable=AsyncMock
        ) as mock_init, patch(
            "agentic_dev.orchestrator.engine.commit", new_callable=AsyncMock
        ) as mock_commit, patch(
            "agentic_dev.orchestrator.engine.write_claude_md"
        ) as mock_write, patch.object(
            engine, "_run_sprints", side_effect=AgentRunError("test", "stop")
        ):
            with pytest.raises(AgentRunError):
                await engine.run()

        mock_write.assert_not_called()
        mock_init.assert_not_called()
        mock_commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_workspace_setup_for_remediate_mode(
        self, engine, state_manager, project_dir
    ):
        """Workspace setup is skipped entirely for remediate mode."""
        (project_dir / "frontend").mkdir(parents=True)
        (project_dir / "backend").mkdir(parents=True)
        state = _make_state(
            PipelinePhase.DESIGN_CHECKPOINT,
            project_type="fullstack",
            mode="remediate",
            sprints=[SprintState(sprint_number=1, name="Sprint 1")],
        )
        state_manager.load = MagicMock(return_value=state)

        with patch(
            "agentic_dev.orchestrator.engine.init_repo", new_callable=AsyncMock
        ) as mock_init, patch(
            "agentic_dev.orchestrator.engine.commit", new_callable=AsyncMock
        ) as mock_commit, patch(
            "agentic_dev.orchestrator.engine.write_claude_md"
        ) as mock_write, patch.object(
            engine, "_run_sprints", side_effect=AgentRunError("test", "stop")
        ):
            with pytest.raises(AgentRunError):
                await engine.run()

        mock_write.assert_not_called()
        mock_init.assert_not_called()
        mock_commit.assert_not_called()


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
            _make_claude_result("uat backend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            _make_claude_result("uat frontend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
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
            _make_claude_result("uat backend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            _make_claude_result("uat frontend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
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
            _make_claude_result("uat backend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
            _make_claude_result("uat frontend", cost=0.10),
            _make_claude_result("APPROVED", cost=0.05),
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
            project_type="frontend_only",
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
            _make_claude_result("APPROVED", cost=0.05),
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


class TestCrashResilience:
    """Tests for crash resilience: conditional status, failed_at_step, shutdown."""

    @pytest.mark.asyncio
    async def test_shutdown_event_saves_state_and_raises(
        self, engine, state_manager
    ):
        """When shutdown event is set, engine saves state and raises GracefulShutdown."""
        state = _make_state(PipelinePhase.FEATURE_ANALYSIS)
        state_manager.load = MagicMock(return_value=state)

        with patch(
            "agentic_dev.orchestrator.engine.get_shutdown_event"
        ) as mock_get_event, patch(
            "agentic_dev.orchestrator.engine.install_signal_handlers"
        ):
            mock_event = MagicMock()
            mock_event.is_set.return_value = True
            mock_get_event.return_value = mock_event

            with pytest.raises(GracefulShutdown):
                await engine.run()

            state_manager.save.assert_called()

    @pytest.mark.asyncio
    async def test_feature_analysis_passes_active_session_id(
        self, engine, claude, doc_store
    ):
        """Feature analysis passes state.active_session_id to run_qa_cycle."""
        state = _make_state(
            PipelinePhase.FEATURE_ANALYSIS,
            active_session_id="prev-sess-42",
        )
        doc_store.read = MagicMock(return_value="structured input")

        claude.run.side_effect = [
            _make_claude_result("features", cost=0.20),
            _make_claude_result("APPROVED", cost=0.10),
        ]

        await engine._run_feature_analysis(state)

        first_call = claude.run.call_args_list[0]
        assert first_call.kwargs.get("session_id") == "prev-sess-42"

    @pytest.mark.asyncio
    async def test_feature_analysis_clears_session_id_on_success(
        self, engine, claude, doc_store
    ):
        """Feature analysis clears active_session_id after successful phase."""
        state = _make_state(
            PipelinePhase.FEATURE_ANALYSIS,
            active_session_id="old-sess",
        )
        doc_store.read = MagicMock(return_value="structured input")

        claude.run.side_effect = [
            ClaudeResult(text="features", session_id="new-sess-77", cost_usd=0.20, exit_code=0),
            _make_claude_result("APPROVED", cost=0.10),
        ]

        await engine._run_feature_analysis(state)

        assert state.active_session_id is None


def _make_engine_for_parser_tests(tmp_path, claude_runner):
    """Construct a minimal PipelineEngine usable for _parse_sprint_plan tests."""
    registry = MagicMock(spec=AgentRegistry)
    registry.get = MagicMock(side_effect=lambda name: _make_agent(name))
    doc_store = DocumentStore(tmp_path)
    prompt_renderer = MagicMock(spec=PromptRenderer)
    prompt_renderer.render_agent_prompt = MagicMock(return_value="prompt")
    state_manager = MagicMock(spec=StateManager)
    checkpoint = CheckpointConfig()
    return PipelineEngine(
        claude=claude_runner,
        registry=registry,
        prompt_renderer=prompt_renderer,
        doc_store=doc_store,
        state_manager=state_manager,
        project_dir=tmp_path,
        checkpoint_config=checkpoint,
    )


class TestParseSprintPlan:
    """Tests for PipelineEngine._parse_sprint_plan() with the LLM-parser backend."""

    @staticmethod
    def _parsed(sprints):
        from agentic_dev.state.parser_models import (
            ParsedSprintEntry,
            ParsedSprintPlan,
        )
        return ParsedSprintPlan(
            sprints=[ParsedSprintEntry(**s) for s in sprints],
        )

    @patch("agentic_dev.orchestrator.engine.parse_with_llm", new_callable=AsyncMock)
    async def test_converts_parsed_entries_and_lowercases_services(
        self, mock_parse, tmp_path, claude,
    ) -> None:
        mock_parse.return_value = self._parsed([
            {
                "sprint_number": 1,
                "name": "Auth & Payments",
                "scope_text": "## Sprint 1: Auth & Payments\n- ...",
                "needs_integration": True,
                "integration_services": ["Stripe", "GitHub"],
            },
        ])
        engine = _make_engine_for_parser_tests(tmp_path, claude)

        plan = (
            "## Sprint 1: Auth & Payments\n"
            "- **Needs Integration:** yes\n"
            "- **Integration Services:** Stripe, GitHub\n"
        )
        sprints = await engine._parse_sprint_plan(plan)

        assert len(sprints) == 1
        assert sprints[0].sprint_number == 1
        assert sprints[0].name == "Auth & Payments"
        assert sprints[0].integration_services == ["stripe", "github"]

    @patch("agentic_dev.orchestrator.engine.parse_with_llm", new_callable=AsyncMock)
    async def test_inline_sprint_keyword_does_not_inflate_header_count(
        self, mock_parse, tmp_path, claude,
    ) -> None:
        """Regression: 'Sprint N:' inside a notes paragraph must not be parsed
        as a sprint header. The header sanity-count comes from anchored
        regex; the LLM is asked to ignore narrative references."""
        mock_parse.return_value = self._parsed([
            {"sprint_number": 1, "name": "First", "scope_text": "..."},
            {"sprint_number": 2, "name": "Second", "scope_text": "..."},
        ])
        engine = _make_engine_for_parser_tests(tmp_path, claude)

        plan = (
            "## Sprint 1: First\n"
            "- **Features:** [F001]\n"
            "\n"
            "## Sprint 2: Second\n"
            "- **Features:** [F002]\n"
            "\n"
            "**QA Notes:**\n"
            "1. **Sprint 5 over-constrained dependency on Sprint 4:** "
            "F003 (now Sprint 6) no longer depends on Sprint 5. It depends "
            "only on Sprint 3 and Sprint 4. Sprints 5 and 6 can run in parallel.\n"
        )
        sprints = await engine._parse_sprint_plan(plan)

        assert len(sprints) == 2
        assert {s.sprint_number for s in sprints} == {1, 2}

    @patch("agentic_dev.orchestrator.engine.parse_with_llm", new_callable=AsyncMock)
    async def test_count_mismatch_propagates_as_output_parse_error(
        self, mock_parse, tmp_path, claude,
    ) -> None:
        from agentic_dev.exceptions import OutputParseError as _OPE

        async def _raise(*_, sanity_check, **__):
            sanity_check(self._parsed([
                {"sprint_number": 1, "name": "First", "scope_text": "..."},
            ]))
            raise _OPE(agent_name="sprint_plan_parser", message="should not reach")

        mock_parse.side_effect = _raise
        engine = _make_engine_for_parser_tests(tmp_path, claude)

        plan = "## Sprint 1: First\n## Sprint 2: Second\n"
        with pytest.raises(ValueError, match="count mismatch"):
            await engine._parse_sprint_plan(plan)

    @patch("agentic_dev.orchestrator.engine.parse_with_llm", new_callable=AsyncMock)
    async def test_duplicate_sprint_number_rejected_by_sanity_check(
        self, mock_parse, tmp_path, claude,
    ) -> None:
        async def _invoke(*_, sanity_check, **__):
            sanity_check(self._parsed([
                {"sprint_number": 1, "name": "One", "scope_text": "..."},
                {"sprint_number": 1, "name": "OneAgain", "scope_text": "..."},
            ]))

        mock_parse.side_effect = _invoke
        engine = _make_engine_for_parser_tests(tmp_path, claude)

        plan = "## Sprint 1: First\n## Sprint 1: Second\n"
        with pytest.raises(ValueError, match="duplicate sprint_number"):
            await engine._parse_sprint_plan(plan)

    async def test_raises_when_no_sprint_headers_present(
        self, tmp_path, claude,
    ) -> None:
        engine = _make_engine_for_parser_tests(tmp_path, claude)
        plan = "Just do everything in one sprint."
        with pytest.raises(OutputParseError, match="No '## Sprint N:' headers"):
            await engine._parse_sprint_plan(plan)

    @patch("agentic_dev.orchestrator.engine.parse_with_llm", new_callable=AsyncMock)
    async def test_passes_plan_text_and_schema_to_helper(
        self, mock_parse, tmp_path, claude,
    ) -> None:
        from agentic_dev.state.parser_models import ParsedSprintPlan

        mock_parse.return_value = self._parsed([
            {"sprint_number": 1, "name": "First", "scope_text": "..."},
        ])
        engine = _make_engine_for_parser_tests(tmp_path, claude)

        plan = "## Sprint 1: First\n- **Features:** [F001]\n"
        await engine._parse_sprint_plan(plan)

        kwargs = mock_parse.call_args.kwargs
        assert kwargs["text"] == plan
        assert kwargs["schema_model"] is ParsedSprintPlan
        assert kwargs["claude"] is claude
        assert callable(kwargs["sanity_check"])


class TestPreSprintMCPValidation:
    """Tests for pre-sprint MCP validation logging in _run_sprints()."""

    @pytest.fixture
    def engine(self, tmp_path, claude):
        registry = MagicMock(spec=AgentRegistry)
        registry.get = MagicMock(side_effect=lambda name: _make_agent(name))
        doc_store = DocumentStore(tmp_path)
        prompt_renderer = MagicMock(spec=PromptRenderer)
        prompt_renderer.render_agent_prompt = MagicMock(return_value="prompt")
        state_manager = MagicMock(spec=StateManager)
        checkpoint = CheckpointConfig()
        engine = PipelineEngine(
            claude=claude,
            registry=registry,
            prompt_renderer=prompt_renderer,
            doc_store=doc_store,
            state_manager=state_manager,
            project_dir=tmp_path,
            checkpoint_config=checkpoint,
        )
        return engine

    @patch("agentic_dev.mcp.claude_settings.discover_mcp_servers")
    def test_validate_sprint_mcp_services_returns_warnings(self, mock_discover, engine) -> None:
        """_validate_sprint_mcp_services returns warnings for unconfigured services."""
        from agentic_dev.mcp.claude_settings import ClaudeMCPEnvironment
        mock_discover.return_value = ClaudeMCPEnvironment(servers={})
        sprints = [
            SprintState(sprint_number=1, name="S1", integration_services=["stripe"]),
            SprintState(sprint_number=2, name="S2", integration_services=["nonexistent"]),
        ]
        warnings = engine._validate_sprint_mcp_services(sprints)
        assert any("nonexistent" in w for w in warnings)
        assert any("stripe" in w for w in warnings)

    @patch("agentic_dev.mcp.claude_settings.discover_mcp_servers")
    def test_validate_sprint_mcp_services_no_warnings_when_configured(self, mock_discover, engine) -> None:
        """Services found in Claude Code settings produce no warnings."""
        from agentic_dev.mcp.claude_settings import ClaudeMCPEnvironment, MCPServerEntry
        mock_discover.return_value = ClaudeMCPEnvironment(
            servers={"figma": MCPServerEntry(name="figma", transport="stdio", source="global")}
        )
        sprints = [
            SprintState(sprint_number=1, name="S1", integration_services=["figma"]),
        ]
        warnings = engine._validate_sprint_mcp_services(sprints)
        assert warnings == []

    def test_validate_sprint_mcp_services_empty_services(self, engine) -> None:
        """Sprints with no integration services produce no warnings."""
        sprints = [
            SprintState(sprint_number=1, name="S1", integration_services=[]),
        ]
        warnings = engine._validate_sprint_mcp_services(sprints)
        assert warnings == []


class TestCommitDocsChanges:
    """Tests for _commit_docs_changes backward-compat and commit behavior."""

    @pytest.mark.asyncio
    async def test_commits_when_changes_exist(self, engine, tmp_path) -> None:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / ".git").mkdir()
        engine._doc_store.docs_dir = docs_dir

        with patch(
            "agentic_dev.orchestrator.engine.has_changes",
            new_callable=AsyncMock, return_value=True,
        ) as mock_has, patch(
            "agentic_dev.orchestrator.engine.commit",
            new_callable=AsyncMock,
        ) as mock_commit:
            await engine._commit_docs_changes("test commit")

        mock_has.assert_called_once_with(docs_dir)
        mock_commit.assert_called_once_with(docs_dir, "test commit")

    @pytest.mark.asyncio
    async def test_inits_repo_when_git_missing(self, engine, tmp_path) -> None:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        engine._doc_store.docs_dir = docs_dir

        with patch(
            "agentic_dev.orchestrator.engine.init_repo",
            new_callable=AsyncMock,
        ) as mock_init, patch(
            "agentic_dev.orchestrator.engine.has_changes",
            new_callable=AsyncMock, return_value=True,
        ), patch(
            "agentic_dev.orchestrator.engine.commit",
            new_callable=AsyncMock,
        ) as mock_commit:
            await engine._commit_docs_changes("test commit")

        mock_init.assert_called_once_with(docs_dir)
        mock_commit.assert_called_once_with(docs_dir, "test commit")

    @pytest.mark.asyncio
    async def test_skips_when_docs_dir_missing(self, engine, tmp_path) -> None:
        docs_dir = tmp_path / "docs"
        engine._doc_store.docs_dir = docs_dir

        with patch(
            "agentic_dev.orchestrator.engine.has_changes",
            new_callable=AsyncMock,
        ) as mock_has:
            await engine._commit_docs_changes("test commit")

        mock_has.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_commit_when_no_changes(self, engine, tmp_path) -> None:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / ".git").mkdir()
        engine._doc_store.docs_dir = docs_dir

        with patch(
            "agentic_dev.orchestrator.engine.has_changes",
            new_callable=AsyncMock, return_value=False,
        ), patch(
            "agentic_dev.orchestrator.engine.commit",
            new_callable=AsyncMock,
        ) as mock_commit:
            await engine._commit_docs_changes("test commit")

        mock_commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_continues_when_commit_raises(self, engine, tmp_path) -> None:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / ".git").mkdir()
        engine._doc_store.docs_dir = docs_dir

        with patch(
            "agentic_dev.orchestrator.engine.has_changes",
            new_callable=AsyncMock, return_value=True,
        ), patch(
            "agentic_dev.orchestrator.engine.commit",
            new_callable=AsyncMock, side_effect=RuntimeError("nothing to commit"),
        ):
            await engine._commit_docs_changes("test commit")


class TestMergeChangeRequest:
    """Tests for _merge_change_request and update-mode context passing."""

    @pytest.mark.asyncio
    async def test_merge_runs_input_updater_qa_cycle_when_change_request_exists(
        self, engine, claude, doc_store
    ):
        """When change_request exists, _merge_change_request runs input_updater with QA cycle."""
        state = _make_state(PipelinePhase.FEATURE_ANALYSIS, mode="update")

        doc_store.read.side_effect = lambda name: {
            "structured_input": "# Structured Input\n- [F001] Auth",
            "change_request": "Add notifications feature",
        }.get(name.replace(".md", ""), "")

        claude.run.side_effect = [
            _make_claude_result(
                "# Structured Input\n- [EXISTING-F001] Auth\n- [F002] Notifications"
            ),
            _make_claude_result("APPROVED", cost=0.05),
        ]

        with patch.object(engine, "_commit_docs_changes", new_callable=AsyncMock):
            await engine._merge_change_request(state)

        # Verify both input_updater and input_updater_qa agents were invoked
        assert claude.run.call_count == 2

        # Verify updated structured_input was written
        doc_store.write.assert_any_call(
            "structured_input",
            "# Structured Input\n- [EXISTING-F001] Auth\n- [F002] Notifications",
        )

        # Verify change_request was deleted after merge
        doc_store.delete.assert_called_once_with("change_request")

    @pytest.mark.asyncio
    async def test_feature_analysis_calls_merge_when_change_request_exists(
        self, engine, doc_store
    ):
        """_run_feature_analysis should call _merge_change_request when change_request doc exists."""
        state = _make_state(PipelinePhase.FEATURE_ANALYSIS, mode="update")

        doc_store.exists.side_effect = lambda name: name.replace(".md", "") == "change_request"

        with patch.object(
            engine, "_merge_change_request", new_callable=AsyncMock
        ) as mock_merge, patch(
            "agentic_dev.orchestrator.engine.run_qa_cycle",
            new_callable=AsyncMock,
            return_value=MagicMock(total_cost=0.1, output="features output"),
        ), patch.object(
            engine, "_commit_docs_changes", new_callable=AsyncMock
        ):
            await engine._run_feature_analysis(state)

        mock_merge.assert_called_once_with(state)

    @pytest.mark.asyncio
    async def test_feature_analysis_skips_merge_when_no_change_request(
        self, engine, doc_store
    ):
        """_run_feature_analysis should NOT call _merge_change_request without change_request doc."""
        state = _make_state(PipelinePhase.FEATURE_ANALYSIS)

        doc_store.exists.return_value = False

        with patch.object(
            engine, "_merge_change_request", new_callable=AsyncMock
        ) as mock_merge, patch(
            "agentic_dev.orchestrator.engine.run_qa_cycle",
            new_callable=AsyncMock,
            return_value=MagicMock(total_cost=0.1, output="features output"),
        ), patch.object(
            engine, "_commit_docs_changes", new_callable=AsyncMock
        ):
            await engine._run_feature_analysis(state)

        mock_merge.assert_not_called()

    def test_update_extra_context_includes_change_request_in_update_mode(
        self, engine, doc_store
    ):
        """_update_extra_context returns change_request when mode is 'update'."""
        state = _make_state(PipelinePhase.FEATURE_ANALYSIS, mode="update")
        doc_store.exists.side_effect = lambda name: name.replace(".md", "") == "user_input"
        doc_store.read.return_value = "Add notifications feature"

        result = engine._update_extra_context(state)

        assert result == {"change_request": "Add notifications feature"}

    def test_update_extra_context_empty_in_new_mode(self, engine, doc_store):
        """_update_extra_context returns empty dict when mode is not 'update'."""
        state = _make_state(PipelinePhase.FEATURE_ANALYSIS, mode="new")

        result = engine._update_extra_context(state)

        assert result == {}


class TestDesignChangesContext:
    """Tests for design_changes flowing through _update_extra_context."""

    def test_update_extra_context_includes_design_changes(self, engine, doc_store):
        """_update_extra_context returns design_changes when doc exists."""
        state = _make_state(PipelinePhase.FEATURE_ANALYSIS, mode="update")
        doc_store.exists.side_effect = lambda name: name.replace(".md", "") in {
            "user_input", "design_changes",
        }
        doc_store.read.side_effect = lambda name: {
            "user_input": "Add dark mode",
            "design_changes": "## Components\n- Button: color changed #3B82F6 → #2563EB",
        }.get(name.replace(".md", ""), "")

        result = engine._update_extra_context(state)

        assert result["change_request"] == "Add dark mode"
        assert "Button" in result["design_changes"]

    def test_update_extra_context_design_changes_without_update_mode(self, engine, doc_store):
        """design_changes is included even outside update mode (could be from initial Figma)."""
        state = _make_state(PipelinePhase.ARCHITECTURE, mode="new")
        doc_store.exists.side_effect = lambda name: name.replace(".md", "") == "design_changes"
        doc_store.read.return_value = "## Components\n- Card: added"

        result = engine._update_extra_context(state)

        assert "change_request" not in result
        assert result["design_changes"] == "## Components\n- Card: added"

    def test_update_extra_context_no_design_changes(self, engine, doc_store):
        """No design_changes key when doc does not exist."""
        state = _make_state(PipelinePhase.FEATURE_ANALYSIS, mode="update")
        doc_store.exists.side_effect = lambda name: name.replace(".md", "") == "user_input"
        doc_store.read.return_value = "Add dark mode"

        result = engine._update_extra_context(state)

        assert "design_changes" not in result


class TestUATExtraContext:
    """Tests for UAT receiving extra_context."""

    @pytest.mark.asyncio
    async def test_uat_receives_change_context(self, engine, claude, doc_store):
        """_run_uat should pass extra_context from _update_extra_context."""
        state = _make_state(PipelinePhase.UAT, mode="update")
        doc_store.exists.side_effect = lambda name: name.replace(".md", "") in {
            "features", "frontend_spec", "user_input", "design_changes",
        }
        doc_store.read.side_effect = lambda name: {
            "features": "# Features",
            "frontend_spec": "# Frontend Spec",
            "user_input": "Add dark mode",
            "design_changes": "## Components\n- Button: color changed",
        }.get(name.replace(".md", ""), "")

        claude.run.return_value = _make_claude_result("# UAT Report\n## Overall Result: PASS")

        with patch.object(engine, "_commit_docs_changes", new_callable=AsyncMock):
            await engine._run_uat(state)

        # Verify the prompt renderer was called with extra_context
        render_call = engine._prompt_renderer.render_agent_prompt
        call_kwargs = render_call.call_args.kwargs if render_call.call_args.kwargs else {}
        call_args_dict = dict(zip(
            ["template_name", "input_documents", "constraints", "extra_context"],
            render_call.call_args.args,
        )) if render_call.call_args.args else {}
        all_kwargs = {**call_args_dict, **call_kwargs}
        assert all_kwargs.get("extra_context") is not None
        assert "change_request" in all_kwargs["extra_context"]
        assert "design_changes" in all_kwargs["extra_context"]


class TestParseSprintPlanScopeText:
    """Tests for scope_text propagation through the LLM parser."""

    @staticmethod
    def _parsed(sprints):
        from agentic_dev.state.parser_models import (
            ParsedSprintEntry,
            ParsedSprintPlan,
        )
        return ParsedSprintPlan(
            sprints=[ParsedSprintEntry(**s) for s in sprints],
        )

    @patch("agentic_dev.orchestrator.engine.parse_with_llm", new_callable=AsyncMock)
    async def test_scope_text_from_llm_preserved_on_sprint_state(
        self, mock_parse, tmp_path, claude,
    ) -> None:
        block = (
            "Sprint 1: Auth & Payments\n"
            "- **Type:** new\n"
            "- **Features:** [F001], [F002]\n"
            "- **Needs Integration:** yes\n"
            "- **Integration Services:** Stripe"
        )
        mock_parse.return_value = self._parsed([
            {
                "sprint_number": 1,
                "name": "Auth & Payments",
                "scope_text": block,
                "needs_integration": True,
                "integration_services": ["Stripe"],
            },
        ])
        engine = _make_engine_for_parser_tests(tmp_path, claude)

        plan = "## " + block + "\n"
        sprints = await engine._parse_sprint_plan(plan)

        assert len(sprints) == 1
        assert sprints[0].scope_text == block

    @patch("agentic_dev.orchestrator.engine.parse_with_llm", new_callable=AsyncMock)
    async def test_multiple_sprints_have_separate_scope_texts(
        self, mock_parse, tmp_path, claude,
    ) -> None:
        mock_parse.return_value = self._parsed([
            {"sprint_number": 1, "name": "Core", "scope_text": "Sprint 1 scope text"},
            {"sprint_number": 2, "name": "Pay", "scope_text": "Sprint 2 scope text"},
        ])
        engine = _make_engine_for_parser_tests(tmp_path, claude)

        plan = "## Sprint 1: Core\n## Sprint 2: Pay\n"
        sprints = await engine._parse_sprint_plan(plan)

        assert len(sprints) == 2
        assert sprints[0].scope_text == "Sprint 1 scope text"
        assert sprints[1].scope_text == "Sprint 2 scope text"


class TestValidateSprintFeatureConventions:
    """Tests for _validate_sprint_feature_conventions."""

    def test_no_warnings_for_clean_plan(self) -> None:
        sprints = [
            SprintState(
                sprint_number=1,
                name="Auth",
                scope_text="## Sprint 1: Auth\n- **Features:** [F001]\n",
            ),
        ]
        warnings = PipelineEngine._validate_sprint_feature_conventions(
            sprints, "## Feature: [F001] Auth\n",
        )
        assert warnings == []

    def test_warns_when_existing_feature_in_sprint(self) -> None:
        sprints = [
            SprintState(
                sprint_number=1,
                name="Auth Rebuild",
                scope_text="## Sprint 1: Auth Rebuild\n- **Features:** [EXISTING-F001]\n",
            ),
        ]
        warnings = PipelineEngine._validate_sprint_feature_conventions(
            sprints, "## Feature: [EXISTING-F001] Auth\n",
        )
        assert len(warnings) == 1
        assert "EXISTING-F001" in warnings[0]
        assert "Sprint 1" in warnings[0]

    def test_warns_when_deleted_feature_has_no_cleanup_sprint(self) -> None:
        sprints = [
            SprintState(
                sprint_number=1,
                name="New Feature",
                scope_text="## Sprint 1: New Feature\n- **Features:** [F002]\n",
            ),
        ]
        features = "## Feature: [DELETED-F003] Old Payment\n**Status: DELETED**\n"
        warnings = PipelineEngine._validate_sprint_feature_conventions(
            sprints, features,
        )
        assert len(warnings) == 1
        assert "DELETED-F003" in warnings[0]
        assert "no cleanup sprint" in warnings[0]

    def test_no_warning_when_deleted_feature_has_cleanup_sprint(self) -> None:
        sprints = [
            SprintState(
                sprint_number=1,
                name="Cleanup",
                scope_text="## Sprint 1: Cleanup\n- **Features:** [DELETED-F003]\n",
            ),
        ]
        features = "## Feature: [DELETED-F003] Old Payment\n**Status: DELETED**\n"
        warnings = PipelineEngine._validate_sprint_feature_conventions(
            sprints, features,
        )
        assert warnings == []

    def test_multiple_violations_reported(self) -> None:
        sprints = [
            SprintState(
                sprint_number=1,
                name="Bad Sprint",
                scope_text="## Sprint 1: Bad Sprint\n- **Features:** [EXISTING-F001], [EXISTING-F002]\n",
            ),
        ]
        features = (
            "## Feature: [EXISTING-F001] Auth\n"
            "## Feature: [DELETED-F005] Legacy\n"
        )
        warnings = PipelineEngine._validate_sprint_feature_conventions(
            sprints, features,
        )
        assert len(warnings) == 2
        assert any("EXISTING-F001" in w for w in warnings)
        assert any("DELETED-F005" in w for w in warnings)


class TestRestoreUnchangedSpecs:
    """Tests for _restore_unchanged_specs during update mode."""

    @pytest.mark.asyncio
    async def test_restores_unchanged_spec_from_git(self, engine, tmp_path):
        """When a regenerated spec matches the git HEAD version, restore it."""
        docs_dir = tmp_path / "project" / "docs"
        docs_dir.mkdir(parents=True)
        engine._doc_store.docs_dir = docs_dir

        committed_content = "# Backend Spec\n## Models\n- User"

        written_docs: dict[str, str] = {}
        engine._doc_store.write = MagicMock(
            side_effect=lambda name, content: written_docs.update({name: content})
        )

        with patch(
            "agentic_dev.orchestrator.engine.get_committed_content",
            new_callable=AsyncMock,
            return_value=committed_content,
        ):
            new_docs = {"backend_spec": "# Backend Spec\n## Models\n- User\n"}
            await engine._restore_unchanged_specs(new_docs)

        assert "backend_spec" in written_docs
        assert written_docs["backend_spec"] == committed_content

    @pytest.mark.asyncio
    async def test_keeps_changed_spec(self, engine, tmp_path):
        """When a regenerated spec differs from git HEAD, don't restore."""
        docs_dir = tmp_path / "project" / "docs"
        docs_dir.mkdir(parents=True)
        engine._doc_store.docs_dir = docs_dir
        engine._doc_store.write = MagicMock()

        with patch(
            "agentic_dev.orchestrator.engine.get_committed_content",
            new_callable=AsyncMock,
            return_value="# Backend Spec\n## Old content",
        ):
            new_docs = {"backend_spec": "# Backend Spec\n## New content with changes\n"}
            await engine._restore_unchanged_specs(new_docs)

        engine._doc_store.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_op_when_file_not_in_git(self, engine, tmp_path):
        """When the file doesn't exist in git HEAD, do nothing."""
        docs_dir = tmp_path / "project" / "docs"
        docs_dir.mkdir(parents=True)
        engine._doc_store.docs_dir = docs_dir
        engine._doc_store.write = MagicMock()

        with patch(
            "agentic_dev.orchestrator.engine.get_committed_content",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await engine._restore_unchanged_specs({"backend_spec": "content"})

        engine._doc_store.write.assert_not_called()
