"""Tests for the agentic-dev CLI."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from agentic_dev.cli import (
    _run_engine_with_rate_limit_resume,
    _run_pipeline,
    _sleep_for_rate_limit_reset,
    app,
)
from agentic_dev.config import AGENTIC_DEV_METADATA_DIR, CONFIG_FILE
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import GracefulShutdown, RateLimitPause
from agentic_dev.orchestrator.checkpoint import CheckpointConfig
from agentic_dev.state.manager import StateManager
from agentic_dev.state.models import PipelinePhase


runner = CliRunner()


@pytest.fixture
def project_with_state(tmp_path: Path) -> Path:
    """Create a project directory with initialised state and config."""
    project_dir = tmp_path / "test-app"
    project_dir.mkdir()
    meta_dir = project_dir / AGENTIC_DEV_METADATA_DIR
    meta_dir.mkdir()
    (meta_dir / "history").mkdir()
    (meta_dir / "logs").mkdir()
    (meta_dir / "sessions").mkdir()
    (project_dir / ".agentic-dev" / "artifacts" / "qa").mkdir(parents=True)
    (project_dir / "frontend").mkdir()
    (project_dir / "backend").mkdir()

    state_mgr = StateManager(project_dir)
    state_mgr.create_initial("test-app")

    config = CheckpointConfig()
    config_path = meta_dir / CONFIG_FILE
    config_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")

    return tmp_path


class TestHelpOutput:
    def test_main_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "agentic-dev" in result.output.lower() or "autonomous" in result.output.lower()

class TestMoreHelpOutput:
    def test_resume_help(self) -> None:
        result = runner.invoke(app, ["resume", "--help"])
        assert result.exit_code == 0
        assert "feedback" in result.output.lower()

    def test_status_help(self) -> None:
        result = runner.invoke(app, ["status", "--help"])
        assert result.exit_code == 0

    def test_config_help(self) -> None:
        result = runner.invoke(app, ["config", "--help"])
        assert result.exit_code == 0

    def test_logs_help(self) -> None:
        result = runner.invoke(app, ["logs", "--help"])
        assert result.exit_code == 0

    def test_cost_help(self) -> None:
        result = runner.invoke(app, ["cost", "--help"])
        assert result.exit_code == 0

class TestRateLimitPauseResume:
    """CLI-level sleep-and-re-enter loop around ``engine.run()``."""

    @pytest.mark.asyncio
    async def test_resumes_engine_after_pause_then_succeeds(self):
        """Engine raises RateLimitPause once; wrapper sleeps, resumes, completes."""
        calls = 0

        async def fake_run() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RateLimitPause(
                    phase="sprinting", wait_seconds=0.01, source="fallback",
                )

        engine = MagicMock()
        engine.run = fake_run
        event_log = MagicMock()
        sleep_fn = AsyncMock(return_value=True)

        await _run_engine_with_rate_limit_resume(
            engine, event_log,
            max_consecutive_pauses=5,
            sleep_fn=sleep_fn,
        )

        assert calls == 2
        sleep_fn.assert_awaited_once_with(0.01)

    @pytest.mark.asyncio
    async def test_consecutive_pause_limit_raises(self):
        """After N consecutive pauses the wrapper re-raises the pause."""

        async def always_pause() -> None:
            raise RateLimitPause(
                phase="sprinting", wait_seconds=0.01, source="fallback",
            )

        engine = MagicMock()
        engine.run = always_pause
        event_log = MagicMock()
        sleep_fn = AsyncMock(return_value=True)

        with pytest.raises(RateLimitPause):
            await _run_engine_with_rate_limit_resume(
                engine, event_log,
                max_consecutive_pauses=2,
                sleep_fn=sleep_fn,
            )

        # Exactly 2 sleeps accepted; the 3rd pause exceeds the cap and re-raises.
        assert sleep_fn.await_count == 2

    @pytest.mark.asyncio
    async def test_shutdown_during_pause_raises_graceful_shutdown(self):
        """sleep_fn returning False (shutdown fired) escalates to GracefulShutdown."""

        async def always_pause() -> None:
            raise RateLimitPause(
                phase="sprinting", wait_seconds=1.0, source="fallback",
            )

        engine = MagicMock()
        engine.run = always_pause
        event_log = MagicMock()
        sleep_fn = AsyncMock(return_value=False)

        with pytest.raises(GracefulShutdown):
            await _run_engine_with_rate_limit_resume(
                engine, event_log,
                max_consecutive_pauses=5,
                sleep_fn=sleep_fn,
            )

    @pytest.mark.asyncio
    async def test_passes_through_other_exceptions_unchanged(self):
        """Non-pause exceptions propagate without retry."""

        async def explode() -> None:
            raise ValueError("boom")

        engine = MagicMock()
        engine.run = explode
        event_log = MagicMock()
        sleep_fn = AsyncMock()

        with pytest.raises(ValueError, match="boom"):
            await _run_engine_with_rate_limit_resume(
                engine, event_log, sleep_fn=sleep_fn,
            )

        sleep_fn.assert_not_called()


class TestSleepForRateLimitReset:
    """Shutdown-aware sleep helper."""

    @pytest.mark.asyncio
    async def test_returns_true_when_full_wait_elapses(self):
        """With a very small wait and idle shutdown event, returns True."""
        import agentic_dev.orchestrator.shutdown as shutdown_mod
        shutdown_mod._shutdown_event = asyncio.Event()

        result = await _sleep_for_rate_limit_reset(
            wait_seconds=0.02, poll_interval=0.01,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_shutdown_event_set(self):
        """When the shutdown event is already set, returns False immediately."""
        import agentic_dev.orchestrator.shutdown as shutdown_mod
        shutdown_mod._shutdown_event = asyncio.Event()
        shutdown_mod._shutdown_event.set()

        result = await _sleep_for_rate_limit_reset(
            wait_seconds=10.0, poll_interval=0.01,
        )
        assert result is False

        # Clean up so later tests don't see a pre-set event
        shutdown_mod._shutdown_event = asyncio.Event()


class TestRunPipelineEngineConstruction:
    """Regression tests for `_run_pipeline`'s `PipelineEngine` construction.

    The track-model refactor (commit 10f228f) removed `directory_map` from
    `ProjectConfig` and dropped the matching parameter from
    `PipelineEngine.__init__`. `_run_pipeline` previously passed
    `directory_map=project_config.directory_map`, which raised
    `AttributeError` against the new Pydantic model and crashed every
    `agentic-dev resume` invocation. These tests pin the constructor contract.
    """

    def test_run_pipeline_does_not_pass_directory_map_kwarg(
        self, project_with_state: Path,
    ) -> None:
        project_dir = project_with_state / "test-app"
        state = StateManager(project_dir).load()

        with patch(
            "agentic_dev.orchestrator.engine.PipelineEngine"
        ) as mock_engine_cls, patch(
            "agentic_dev.cli._run_engine_with_rate_limit_resume"
        ), patch("agentic_dev.cli.asyncio.run"):
            _run_pipeline(project_dir, state)

        assert mock_engine_cls.call_count == 1
        _, kwargs = mock_engine_cls.call_args
        assert "directory_map" not in kwargs, (
            "PipelineEngine no longer accepts `directory_map` — kwarg must not be passed"
        )

    def test_run_pipeline_passes_expected_engine_kwargs(
        self, project_with_state: Path,
    ) -> None:
        """Lock in the kwargs `_run_pipeline` is contracted to forward."""
        project_dir = project_with_state / "test-app"
        state = StateManager(project_dir).load()

        with patch(
            "agentic_dev.orchestrator.engine.PipelineEngine"
        ) as mock_engine_cls, patch(
            "agentic_dev.cli._run_engine_with_rate_limit_resume"
        ), patch("agentic_dev.cli.asyncio.run"):
            _run_pipeline(project_dir, state)

        _, kwargs = mock_engine_cls.call_args
        assert set(kwargs) == {
            "project_dir",
            "claude",
            "registry",
            "doc_store",
            "prompt_renderer",
            "state_manager",
            "checkpoint_config",
        }


# ---------------------------------------------------------------------------
# `work` command — cwd-based onboarding and state-transition dispatch
# ---------------------------------------------------------------------------


def _claude_result(text: str):
    from agentic_dev.claude.runner import ClaudeResult

    return ClaudeResult(
        text=text,
        session_id="test",
        cost_usd=0.0,
        exit_code=0,
        raw_json={},
    )


def _fake_discovery_result(tracks):
    from agentic_dev.discovery.agent import DiscoveryResult

    return DiscoveryResult(tracks=tracks, reasoning="fake", raw_response="{}")


class TestWorkCommandOnboarding:
    """First-run behaviour of ``agentic-dev work``."""

    def test_uses_yaml_override_when_present(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        project = tmp_path / "skillsbloom"
        project.mkdir()
        (project / "backend").mkdir()
        (project / "backend" / "main.py").write_text("# fastapi\n")
        (project / "agentic-dev.yaml").write_text(
            "tracks:\n"
            "  - name: backend\n    path: backend\n    kind: api\n    uat_kind: api\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(project)

        with patch("agentic_dev.cli._run_pipeline") as mock_run, patch(
            "agentic_dev.cli._analyze_existing_tracks"
        ) as mock_analyse, patch(
            "agentic_dev.discovery.agent.discover_tracks"
        ) as mock_discover:
            result = runner.invoke(app, ["work", "do the thing"])

        assert result.exit_code == 0, result.output
        mock_discover.assert_not_called()
        mock_run.assert_called_once()
        mock_analyse.assert_called_once()
        # Config persisted the override tracks.
        cfg_path = project / AGENTIC_DEV_METADATA_DIR / CONFIG_FILE
        cfg = json.loads(cfg_path.read_text())
        assert [t["name"] for t in cfg["tracks"]] == ["backend"]

    def test_runs_discovery_when_no_override(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from agentic_dev.tracks import Track

        project = tmp_path / "fresh"
        project.mkdir()
        (project / "main.py").write_text("print('hi')\n")
        monkeypatch.chdir(project)

        discover_mock = AsyncMock(
            return_value=_fake_discovery_result(
                [Track(name="app", path=".", kind="api", uat_kind="api")]
            )
        )

        with patch("agentic_dev.cli._run_pipeline") as mock_run, patch(
            "agentic_dev.cli._analyze_existing_tracks"
        ), patch(
            "agentic_dev.discovery.discover_tracks", discover_mock
        ):
            result = runner.invoke(app, ["work", "build it"])

        assert result.exit_code == 0, result.output
        discover_mock.assert_awaited_once()
        mock_run.assert_called_once()

    def test_runs_analyser_on_non_empty_tracks(
        self, tmp_path: Path, monkeypatch
    ) -> None:

        project = tmp_path / "two-track"
        project.mkdir()
        (project / "backend").mkdir()
        (project / "backend" / "main.py").write_text("# fastapi\n")
        (project / "frontend").mkdir()
        (project / "frontend" / "package.json").write_text('{"name": "x"}\n')
        (project / "agentic-dev.yaml").write_text(
            "tracks:\n"
            "  - name: backend\n    path: backend\n    kind: api\n    uat_kind: api\n"
            "  - name: frontend\n    path: frontend\n    kind: web\n    uat_kind: web\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(project)

        analyse_mock = AsyncMock(
            return_value=[
                _claude_result("# Backend Analysis\n"),
                _claude_result("# Frontend Analysis\n"),
            ]
        )

        with patch("agentic_dev.cli._run_pipeline"), patch(
            "agentic_dev.onboarding.analyzer.analyze_codebases", analyse_mock
        ):
            result = runner.invoke(app, ["work", "add referrals"])

        assert result.exit_code == 0, result.output
        analyse_mock.assert_awaited_once()
        sources = analyse_mock.await_args.args[1]
        assert {Path(s.value).name for s in sources} == {"backend", "frontend"}

        artifacts = project / AGENTIC_DEV_METADATA_DIR / "artifacts"
        assert (artifacts / "track_backend_analysis.md").is_file()
        assert (artifacts / "track_frontend_analysis.md").is_file()
        combined = (artifacts / "existing_code_analyses.md").read_text()
        assert "backend (api)" in combined
        assert "frontend (web)" in combined

    def test_skips_analyser_when_all_tracks_empty(
        self, tmp_path: Path, monkeypatch
    ) -> None:

        project = tmp_path / "greenfield"
        project.mkdir()
        (project / "agentic-dev.yaml").write_text(
            "tracks:\n  - name: app\n    path: .\n    kind: web\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(project)

        analyse_mock = AsyncMock(return_value=[])

        with patch("agentic_dev.cli._run_pipeline"), patch(
            "agentic_dev.onboarding.analyzer.analyze_codebases", analyse_mock
        ):
            result = runner.invoke(app, ["work", "build a todo app"])

        assert result.exit_code == 0, result.output
        analyse_mock.assert_not_called()
        artifacts = project / AGENTIC_DEV_METADATA_DIR / "artifacts"
        assert not (artifacts / "existing_code_analyses.md").exists()

    def test_first_run_without_requirements_fails(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        project = tmp_path / "p"
        project.mkdir()
        (project / "agentic-dev.yaml").write_text(
            "tracks:\n  - name: app\n    path: .\n    kind: web\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(project)

        # No prompt, no --from-file, no --from-figma, no stdin input.
        result = runner.invoke(app, ["work"], input="")

        assert result.exit_code == 1
        assert "no requirements provided" in result.output.lower()


class TestWorkCommandDispatch:
    """State-transition behaviour on subsequent ``agentic-dev work`` calls."""

    def _seed_project(self, tmp_path: Path, phase: PipelinePhase) -> Path:
        from agentic_dev.config import ProjectConfig, save_project_config
        from agentic_dev.workspace.manager import ensure_scaffold

        project = tmp_path / "already-running"
        ensure_scaffold(project)
        save_project_config(project, ProjectConfig(app_name="already-running"))
        sm = StateManager(project)
        state = sm.create_initial("already-running")
        state.phase = phase
        if phase == PipelinePhase.FAILED:
            # ``resume_from_failure`` requires a recorded failure point.
            state.failed_at_phase = PipelinePhase.SPRINTING
        sm.save(state)
        return project

    def test_complete_project_enqueues_update(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        project = self._seed_project(tmp_path, PipelinePhase.COMPLETE)
        monkeypatch.chdir(project)

        with patch("agentic_dev.cli._start_update_cycle") as mock_update:
            result = runner.invoke(app, ["work", "add a /version endpoint"])

        assert result.exit_code == 0, result.output
        mock_update.assert_called_once()
        kwargs = mock_update.call_args.kwargs
        assert kwargs["mode"] == "update"
        assert kwargs["restart_phase"] == PipelinePhase.FEATURE_ANALYSIS
        assert kwargs["is_targeted"] is True
        assert kwargs["change_input"] == "add a /version endpoint"

    def test_failed_project_resumes_with_feedback(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        project = self._seed_project(tmp_path, PipelinePhase.FAILED)
        monkeypatch.chdir(project)

        with patch("agentic_dev.cli._run_pipeline") as mock_run:
            result = runner.invoke(app, ["work", "try the /version idea again"])

        assert result.exit_code == 0, result.output
        mock_run.assert_called_once()
        # The new prompt was injected as checkpoint feedback before resumption.
        state = StateManager(project).load()
        assert state.checkpoint_feedback == "try the /version idea again"

    def test_mid_pipeline_errors(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        project = self._seed_project(tmp_path, PipelinePhase.ARCHITECTURE)
        monkeypatch.chdir(project)

        with patch("agentic_dev.cli._run_pipeline") as mock_run, patch(
            "agentic_dev.cli._start_update_cycle"
        ) as mock_update:
            result = runner.invoke(app, ["work", "another change"])

        assert result.exit_code == 1
        assert "in progress" in result.output.lower()
        mock_run.assert_not_called()
        mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# Cwd-based commands: status / config / resume / remediate / cost / logs / tracks
# ---------------------------------------------------------------------------


def _seed_minimal_project(tmp_path: Path, phase: PipelinePhase = PipelinePhase.IDLE) -> Path:
    """Create a minimal agentic-dev project at ``tmp_path/proj`` for cwd tests."""
    from agentic_dev.config import ProjectConfig, save_project_config
    from agentic_dev.workspace.manager import ensure_scaffold

    project = tmp_path / "proj"
    ensure_scaffold(project)
    save_project_config(project, ProjectConfig(app_name="proj"))
    sm = StateManager(project)
    state = sm.create_initial("proj")
    state.phase = phase
    sm.save(state)
    return project


class TestCwdCommandsResolveProject:
    """Every cwd-based command should error cleanly when run outside a project."""

    @pytest.mark.parametrize("command", [
        ["status"],
        ["resume"],
        ["remediate"],
        ["config"],
        ["logs"],
        ["cost"],
        ["tracks"],
    ])
    def test_errors_outside_managed_project(
        self, command, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, command)
        assert result.exit_code == 1
        assert "no agentic-dev project found" in result.output.lower()


class TestStatusCommand:
    def test_displays_phase(self, tmp_path: Path, monkeypatch) -> None:
        project = _seed_minimal_project(tmp_path, PipelinePhase.SPRINTING)
        monkeypatch.chdir(project)
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0, result.output
        assert "SPRINTING" in result.output


class TestResumeCommand:
    def test_injects_feedback(self, tmp_path: Path, monkeypatch) -> None:
        project = _seed_minimal_project(tmp_path, PipelinePhase.ARCHITECTURE)
        monkeypatch.chdir(project)
        with patch("agentic_dev.cli._run_pipeline") as mock_run:
            result = runner.invoke(app, ["resume", "--feedback", "try harder"])
        assert result.exit_code == 0, result.output
        mock_run.assert_called_once()
        state = StateManager(project).load()
        assert state.checkpoint_feedback == "try harder"

    def test_failed_state_auto_recovers(self, tmp_path: Path, monkeypatch) -> None:
        project = _seed_minimal_project(tmp_path, PipelinePhase.FAILED)
        state = StateManager(project).load()
        state.failed_at_phase = PipelinePhase.SPRINTING
        StateManager(project).save(state)
        monkeypatch.chdir(project)
        with patch("agentic_dev.cli._run_pipeline"):
            result = runner.invoke(app, ["resume"])
        assert result.exit_code == 0, result.output
        state = StateManager(project).load()
        assert state.phase == PipelinePhase.SPRINTING


class TestRemediateCommand:
    def test_requires_complete_state(self, tmp_path: Path, monkeypatch) -> None:
        project = _seed_minimal_project(tmp_path, PipelinePhase.SPRINTING)
        monkeypatch.chdir(project)
        result = runner.invoke(app, ["remediate"])
        assert result.exit_code == 1
        assert "COMPLETE" in result.output

    def test_requires_uat_report(self, tmp_path: Path, monkeypatch) -> None:
        project = _seed_minimal_project(tmp_path, PipelinePhase.COMPLETE)
        monkeypatch.chdir(project)
        result = runner.invoke(app, ["remediate"])
        assert result.exit_code == 1
        assert "uat report" in result.output.lower()

    def test_remediate_runs_update_cycle(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        project = _seed_minimal_project(tmp_path, PipelinePhase.COMPLETE)
        DocumentStore(project).write(
            "uat_report",
            "## Overall Result: FAIL\nSomething broke",
        )
        monkeypatch.chdir(project)
        with patch("agentic_dev.cli._start_update_cycle") as mock_update:
            result = runner.invoke(app, ["remediate"])
        assert result.exit_code == 0, result.output
        mock_update.assert_called_once()
        assert mock_update.call_args.kwargs["mode"] == "remediate"
        assert (
            mock_update.call_args.kwargs["restart_phase"]
            == PipelinePhase.INPUT_PROCESSING
        )


class TestConfigCommand:
    def test_sets_autonomy(self, tmp_path: Path, monkeypatch) -> None:
        project = _seed_minimal_project(tmp_path)
        monkeypatch.chdir(project)
        result = runner.invoke(app, ["config", "--autonomy", "full"])
        assert result.exit_code == 0, result.output

    def test_sets_individual_checkpoints(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        project = _seed_minimal_project(tmp_path)
        monkeypatch.chdir(project)
        result = runner.invoke(
            app, ["config", "--checkpoints", "after_design,before_uat"]
        )
        assert result.exit_code == 0, result.output


class TestCostCommand:
    def test_no_runs_message(self, tmp_path: Path, monkeypatch) -> None:
        project = _seed_minimal_project(tmp_path)
        monkeypatch.chdir(project)
        result = runner.invoke(app, ["cost"])
        assert result.exit_code == 0, result.output
        assert "No agent runs" in result.output


class TestLogsCommand:
    def test_no_logs_message(self, tmp_path: Path, monkeypatch) -> None:
        project = _seed_minimal_project(tmp_path)
        monkeypatch.chdir(project)
        result = runner.invoke(app, ["logs"])
        assert result.exit_code == 0, result.output
        assert "No pipeline runs" in result.output or "No log files" in result.output


class TestTracksCommand:
    def test_shows_persisted_tracks(self, tmp_path: Path, monkeypatch) -> None:
        from agentic_dev.config import (
            load_project_config,
            save_project_config,
        )
        from agentic_dev.tracks import Track

        project = _seed_minimal_project(tmp_path)
        cfg = load_project_config(project)
        cfg.tracks = [
            Track(name="backend", path="backend", kind="api", uat_kind="api"),
            Track(name="frontend", path="frontend", kind="web", uat_kind="web"),
        ]
        save_project_config(project, cfg)
        monkeypatch.chdir(project)

        result = runner.invoke(app, ["tracks"])

        assert result.exit_code == 0, result.output
        assert "backend" in result.output
        assert "frontend" in result.output

    def test_rediscover_overwrites_persisted_tracks(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from agentic_dev.config import load_project_config
        from agentic_dev.tracks import Track

        project = _seed_minimal_project(tmp_path)
        monkeypatch.chdir(project)

        with patch(
            "agentic_dev.cli._resolve_tracks_for_in_place",
            return_value=[
                Track(name="api", path=".", kind="api", uat_kind="api"),
            ],
        ):
            result = runner.invoke(app, ["tracks", "--rediscover"])

        assert result.exit_code == 0, result.output
        cfg = load_project_config(project)
        assert [t.name for t in cfg.tracks] == ["api"]
