"""Tests for the track-based sprint runner."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_dev.agents.base import AgentDefinition, ClaudeConfig
from agentic_dev.agents.registry import AgentRegistry
from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import AgentRunError
from agentic_dev.orchestrator.sprint_runner import SprintResult, SprintRunner
from agentic_dev.prompts.renderer import PromptRenderer
from agentic_dev.state.manager import StateManager
from agentic_dev.state.models import PipelineState, SprintState, SprintStatus
from agentic_dev.tracks import Track, TrackPhase, TrackProgress


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
    store.exists = MagicMock(return_value=False)
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
def fullstack_tracks() -> list[Track]:
    return [
        Track(name="backend", path="backend", kind="api", uat_kind="api"),
        Track(name="frontend", path="frontend", kind="web", uat_kind="web"),
    ]


@pytest.fixture
def runner(claude, registry, doc_store, prompt_renderer, project_dir, fullstack_tracks):
    return SprintRunner(
        claude=claude,
        registry=registry,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        project_dir=project_dir,
        tracks=fullstack_tracks,
    )


@pytest.mark.asyncio
async def test_sprint_runs_each_track_in_order(runner, claude, fullstack_tracks):
    """A sprint with two tracks runs dev+QA for each track."""
    claude.run.side_effect = [
        _make_claude_result("backend code", cost=0.20),
        _make_claude_result("APPROVED", cost=0.10),
        _make_claude_result("frontend code", cost=0.25),
        _make_claude_result("APPROVED", cost=0.10),
    ]
    result = await runner.run_sprint(sprint_number=1, sprint_scope="auth feature")
    assert result.success is True
    assert set(result.track_results.keys()) == {"backend", "frontend"}
    assert claude.run.await_count == 4


@pytest.mark.asyncio
async def test_track_progress_marked_complete(claude, registry, doc_store, prompt_renderer, project_dir, fullstack_tracks):
    """Each track's progress is recorded in sprint_state.track_progress."""
    sprint_state = SprintState(sprint_number=1, name="Sprint 1")
    pipeline_state = PipelineState(
        project_name="t", sprints=[sprint_state], tracks=fullstack_tracks,
    )
    state_manager = MagicMock(spec=StateManager)
    runner = SprintRunner(
        claude=claude,
        registry=registry,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        project_dir=project_dir,
        tracks=fullstack_tracks,
        state_manager=state_manager,
        pipeline_state=pipeline_state,
    )
    claude.run.side_effect = [
        _make_claude_result("backend code", cost=0.20),
        _make_claude_result("APPROVED", cost=0.10),
        _make_claude_result("frontend code", cost=0.25),
        _make_claude_result("APPROVED", cost=0.10),
    ]
    await runner.run_sprint(sprint_number=1, sprint_scope="auth", sprint_state=sprint_state)
    assert sprint_state.track_progress["backend"].phase == TrackPhase.COMPLETE
    assert sprint_state.track_progress["frontend"].phase == TrackPhase.COMPLETE


@pytest.mark.asyncio
async def test_completed_tracks_are_skipped_on_resume(claude, registry, doc_store, prompt_renderer, project_dir, fullstack_tracks):
    """When a track's progress is COMPLETE, its QA cycle is not re-invoked."""
    sprint_state = SprintState(
        sprint_number=1,
        name="Sprint 1",
        track_progress={
            "backend": TrackProgress(track_name="backend", phase=TrackPhase.COMPLETE),
        },
    )
    runner = SprintRunner(
        claude=claude,
        registry=registry,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        project_dir=project_dir,
        tracks=fullstack_tracks,
    )
    claude.run.side_effect = [
        _make_claude_result("frontend code", cost=0.25),
        _make_claude_result("APPROVED", cost=0.10),
    ]
    result = await runner.run_sprint(
        sprint_number=1, sprint_scope="auth", sprint_state=sprint_state,
    )
    assert "backend" not in result.track_results
    assert "frontend" in result.track_results
    assert claude.run.await_count == 2


@pytest.mark.asyncio
async def test_dev_failure_persists_resume_cursor(claude, registry, doc_store, prompt_renderer, project_dir, fullstack_tracks):
    """A failed dev agent's session/stage is persisted to the pipeline cursor so
    the next resume continues it rather than restarting the track."""
    sprint_state = SprintState(sprint_number=1, name="Sprint 1")
    pipeline_state = PipelineState(
        project_name="t", sprints=[sprint_state], tracks=fullstack_tracks,
    )
    state_manager = MagicMock(spec=StateManager)
    runner = SprintRunner(
        claude=claude,
        registry=registry,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        project_dir=project_dir,
        tracks=fullstack_tracks,
        state_manager=state_manager,
        pipeline_state=pipeline_state,
    )
    claude.run.side_effect = AgentRunError(
        "developer", "stalled", session_id="dev-sess",
    )

    result = await runner.run_sprint(
        sprint_number=1, sprint_scope="auth", sprint_state=sprint_state,
    )

    assert result.success is False
    assert pipeline_state.active_session_id == "dev-sess"
    assert pipeline_state.active_qa_stage == "action"


@pytest.mark.asyncio
async def test_resume_cursor_passed_to_in_flight_track(claude, registry, doc_store, prompt_renderer, project_dir, fullstack_tracks):
    """On resume, the cursor is applied to the first not-complete track (the one
    that failed) and cleared once it completes."""
    sprint_state = SprintState(
        sprint_number=1,
        name="Sprint 1",
        track_progress={
            "backend": TrackProgress(track_name="backend", phase=TrackPhase.COMPLETE),
        },
    )
    pipeline_state = PipelineState(
        project_name="t",
        sprints=[sprint_state],
        tracks=fullstack_tracks,
        active_session_id="resume-sess",
        active_qa_stage="action",
    )
    state_manager = MagicMock(spec=StateManager)
    runner = SprintRunner(
        claude=claude,
        registry=registry,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        project_dir=project_dir,
        tracks=fullstack_tracks,
        state_manager=state_manager,
        pipeline_state=pipeline_state,
    )
    claude.run.side_effect = [
        _make_claude_result("frontend code", cost=0.25),
        _make_claude_result("APPROVED", cost=0.10),
    ]

    await runner.run_sprint(
        sprint_number=1, sprint_scope="auth", sprint_state=sprint_state,
    )

    # backend skipped; frontend (in-flight) resumed the session.
    assert claude.run.call_args_list[0].kwargs.get("session_id") == "resume-sess"
    # Cursor cleared once the track completes.
    assert pipeline_state.active_session_id is None
    assert pipeline_state.active_qa_stage is None


@pytest.mark.asyncio
async def test_tracks_in_scope_filters_iteration(claude, registry, doc_store, prompt_renderer, project_dir, fullstack_tracks):
    """Sprint runs only tracks listed in sprint_state.tracks_in_scope."""
    sprint_state = SprintState(
        sprint_number=1,
        name="Sprint 1",
        tracks_in_scope=["backend"],
    )
    runner = SprintRunner(
        claude=claude,
        registry=registry,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        project_dir=project_dir,
        tracks=fullstack_tracks,
    )
    claude.run.side_effect = [
        _make_claude_result("backend code", cost=0.20),
        _make_claude_result("APPROVED", cost=0.10),
    ]
    result = await runner.run_sprint(
        sprint_number=1, sprint_scope="auth", sprint_state=sprint_state,
    )
    assert set(result.track_results.keys()) == {"backend"}


@pytest.mark.asyncio
async def test_single_track_project(claude, registry, doc_store, prompt_renderer, project_dir):
    """A project with a single track runs that track only."""
    tracks = [Track(name="app", path=".", kind="generic")]
    runner = SprintRunner(
        claude=claude,
        registry=registry,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        project_dir=project_dir,
        tracks=tracks,
    )
    claude.run.side_effect = [
        _make_claude_result("app code", cost=0.20),
        _make_claude_result("APPROVED", cost=0.10),
    ]
    result = await runner.run_sprint(sprint_number=1, sprint_scope="auth")
    assert result.success is True
    assert list(result.track_results.keys()) == ["app"]
    assert claude.run.await_count == 2


@pytest.mark.asyncio
async def test_integration_phase_runs_after_tracks(claude, registry, doc_store, prompt_renderer, project_dir, fullstack_tracks):
    """When needs_integration=True the integration cycle runs after track cycles."""
    runner = SprintRunner(
        claude=claude,
        registry=registry,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        project_dir=project_dir,
        tracks=fullstack_tracks,
    )
    claude.run.side_effect = [
        _make_claude_result("backend code", cost=0.10),
        _make_claude_result("APPROVED", cost=0.05),
        _make_claude_result("frontend code", cost=0.10),
        _make_claude_result("APPROVED", cost=0.05),
        _make_claude_result("integration guide", cost=0.10),
        _make_claude_result("APPROVED", cost=0.05),
    ]
    result = await runner.run_sprint(
        sprint_number=1, sprint_scope="auth", needs_integration=True,
    )
    assert result.integration_result is not None


@pytest.mark.asyncio
async def test_integration_capture_is_file_based(
    claude, registry, doc_store, prompt_renderer, project_dir, fullstack_tracks,
):
    """The integration guide is captured from an engine-controlled file (the
    agent's real deliverable), not its summary stdout. The engine pins the path
    and exposes it to the template as ``integration_guide_path``."""
    runner = SprintRunner(
        claude=claude,
        registry=registry,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        project_dir=project_dir,
        tracks=fullstack_tracks,
    )
    captured: dict = {}

    async def fake_qa(**kwargs):
        captured["action_output_path"] = kwargs.get("action_output_path")
        captured["integration_guide_path"] = (
            (kwargs.get("extra_context") or {}).get("integration_guide_path")
        )
        return MagicMock(total_cost=0.1, session_id="s")

    with patch(
        "agentic_dev.orchestrator.sprint_runner.run_qa_cycle",
        new=AsyncMock(side_effect=fake_qa),
    ):
        await runner._run_integration(
            sprint_number=3,
            sprint_scope="auth",
            feature_ids={"F001"},
            shared_context={},
            sprint_state=None,
        )

    path = captured["action_output_path"]
    assert path is not None, "integration did not pin a guide file path"
    path = Path(path)
    assert path.name == "integration_guide_sprint_3.md"
    assert str(path).startswith(str(project_dir / ".agentic-dev" / "artifacts"))
    assert captured["integration_guide_path"] == str(path)


@pytest.mark.asyncio
async def test_rolling_summary_uses_track_names(claude, registry, prompt_renderer, project_dir, fullstack_tracks, tmp_path):
    """Rolling summary aggregates per-track sprint outputs (not hardcoded backend/frontend)."""
    real_store = DocumentStore(tmp_path / "proj")
    runner = SprintRunner(
        claude=claude,
        registry=registry,
        doc_store=real_store,
        prompt_renderer=prompt_renderer,
        project_dir=project_dir,
        tracks=fullstack_tracks,
    )
    real_store.write("sprint_1_backend", "backend output\nline2")
    real_store.write("sprint_1_frontend", "frontend output\nline2")
    runner._update_rolling_summary(1)
    summary = real_store.read("sprint_rolling_summary")
    assert "### Sprint 1 (backend)" in summary
    assert "### Sprint 1 (frontend)" in summary


def test_tracks_for_sprint_returns_all_when_unscoped(runner, fullstack_tracks):
    sprint_state = SprintState(sprint_number=1, name="s1")
    assert runner._tracks_for_sprint(sprint_state) == fullstack_tracks


def test_tracks_for_sprint_filters_by_scope(runner, fullstack_tracks):
    sprint_state = SprintState(
        sprint_number=1, name="s1", tracks_in_scope=["frontend"],
    )
    result = runner._tracks_for_sprint(sprint_state)
    assert [t.name for t in result] == ["frontend"]


def test_tracks_for_sprint_returns_all_when_state_is_none(runner, fullstack_tracks):
    assert runner._tracks_for_sprint(None) == fullstack_tracks


def test_read_track_spec_returns_empty_when_missing(runner, doc_store):
    doc_store.exists.return_value = False
    assert runner._read_track_spec("nonexistent", set(), 1) == ""


def test_sprint_result_dataclass():
    result = SprintResult(
        sprint_number=1, success=True, total_cost=1.5,
        track_results={}, integration_result=None,
    )
    assert result.sprint_number == 1
    assert result.success is True
    assert result.total_cost == 1.5


@pytest.mark.asyncio
async def test_sprint_state_status_complete_after_run(claude, registry, doc_store, prompt_renderer, project_dir, fullstack_tracks):
    sprint_state = SprintState(sprint_number=1, name="s1")
    runner = SprintRunner(
        claude=claude,
        registry=registry,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        project_dir=project_dir,
        tracks=fullstack_tracks,
    )
    claude.run.side_effect = [
        _make_claude_result("backend code", cost=0.20),
        _make_claude_result("APPROVED", cost=0.10),
        _make_claude_result("frontend code", cost=0.25),
        _make_claude_result("APPROVED", cost=0.10),
    ]
    await runner.run_sprint(
        sprint_number=1, sprint_scope="auth", sprint_state=sprint_state,
    )
    assert sprint_state.status == SprintStatus.COMPLETE


class TestSharedContextFigmaAnnotations:
    """`_build_shared_context` must thread `figma_annotations` into the developer
    context dict when the doc store has the document."""

    def test_figma_annotations_threaded_when_doc_exists(self, runner, doc_store):
        doc_store.exists.side_effect = lambda name: name == "figma_annotations"
        doc_store.read.side_effect = lambda name: (
            "# Figma Annotations\n- Hero: 44px tall"
            if name == "figma_annotations"
            else f"content of {name}"
        )

        ctx = runner._build_shared_context(sprint_state=None)

        assert "figma_annotations" in ctx
        assert "44px tall" in ctx["figma_annotations"]

    def test_figma_annotations_absent_when_doc_missing(self, runner, doc_store):
        doc_store.exists.side_effect = lambda name: False

        ctx = runner._build_shared_context(sprint_state=None)

        assert "figma_annotations" not in ctx


class TestScopeDropEvents:
    """Non-silent scoping: dropped spec sections are surfaced as events."""

    _SPEC = (
        "# Frontend Spec\n## Pages\n"
        "### [P001] Login\n- **Features:** [F001]\n- x\n"
        "### [P002] Dash\n- **Features:** [F002]\n- y\n"
    )

    def test_read_track_spec_emits_scope_drop_for_filtered_section(
        self, runner, doc_store
    ):
        from agentic_dev.logging.events import ScopeDropEvent

        doc_store.exists = MagicMock(side_effect=lambda n: n == "frontend_spec")
        doc_store.read = MagicMock(
            side_effect=lambda n: self._SPEC if n == "frontend_spec"
            else f"content of {n}"
        )
        events: list = []
        with patch(
            "agentic_dev.orchestrator.sprint_runner.emit",
            side_effect=lambda _logger, event: events.append(event),
        ):
            scoped = runner._read_track_spec("frontend", {"F001"}, 2)

        assert "[P001] Login" in scoped and "[P002] Dash" not in scoped
        drops = [e for e in events if isinstance(e, ScopeDropEvent)]
        assert len(drops) == 1
        assert "P002" in drops[0].dropped_ids
        assert drops[0].sprint == 2
        assert drops[0].track == "frontend"

    def test_read_track_spec_no_event_when_nothing_dropped(
        self, runner, doc_store
    ):
        from agentic_dev.logging.events import ScopeDropEvent

        doc_store.exists = MagicMock(side_effect=lambda n: n == "frontend_spec")
        doc_store.read = MagicMock(
            side_effect=lambda n: self._SPEC if n == "frontend_spec"
            else f"content of {n}"
        )
        events: list = []
        with patch(
            "agentic_dev.orchestrator.sprint_runner.emit",
            side_effect=lambda _logger, event: events.append(event),
        ):
            runner._read_track_spec("frontend", {"F001", "F002"}, 1)

        assert not [e for e in events if isinstance(e, ScopeDropEvent)]
