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
from agentic_dev.state.models import PipelinePhase, SprintState, SprintStatus


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

    def test_new_help(self) -> None:
        result = runner.invoke(app, ["new", "--help"])
        assert result.exit_code == 0

    def test_new_help_documents_track_flag(self) -> None:
        result = runner.invoke(app, ["new", "--help"])
        assert result.exit_code == 0
        assert "--track" in result.output


class TestTrackFlag:
    """Tests for the --track flag on ``new``."""

    def test_invalid_track_value_rejected(self) -> None:
        result = runner.invoke(app, ["new", "myapp", "--track", ""])
        assert result.exit_code == 1
        assert "Invalid --track" in result.output

    def test_default_track_when_omitted(self, tmp_path: Path) -> None:
        from agentic_dev.config import load_project_config
        from agentic_dev.state.manager import StateManager

        with patch("agentic_dev.cli._run_pipeline"), \
             patch("agentic_dev.cli._collect_user_requirements", return_value="build x"):
            result = runner.invoke(
                app,
                ["new", "myapp", "--path", str(tmp_path)],
            )
        assert result.exit_code == 0, result.output
        state = StateManager(tmp_path / "myapp").load()
        assert [t.name for t in state.tracks] == ["app"]
        cfg = load_project_config(tmp_path / "myapp")
        assert [t.name for t in cfg.tracks] == ["app"]

    def test_explicit_tracks_persisted(self, tmp_path: Path) -> None:
        from agentic_dev.config import load_project_config
        from agentic_dev.state.manager import StateManager

        with patch("agentic_dev.cli._run_pipeline"), \
             patch("agentic_dev.cli._collect_user_requirements", return_value="build x"):
            result = runner.invoke(
                app,
                [
                    "new", "myapp",
                    "--path", str(tmp_path),
                    "--track", "web::web::web::web",
                    "--track", "api::api::api::api",
                ],
            )
        assert result.exit_code == 0, result.output
        state = StateManager(tmp_path / "myapp").load()
        assert {t.name for t in state.tracks} == {"web", "api"}
        cfg = load_project_config(tmp_path / "myapp")
        assert {t.name for t in cfg.tracks} == {"web", "api"}


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


class TestNewCommand:
    @patch("agentic_dev.cli._run_pipeline")
    def test_creates_project_structure(
        self, mock_run_pipeline, tmp_path: Path
    ) -> None:
        """The new command should create base workspace (no frontend/backend yet)."""
        result = runner.invoke(
            app,
            ["new", "my-app", "--path", str(tmp_path)],
            input="Build a todo app\n\n\n",
        )

        assert result.exit_code == 0, result.output
        project_dir = tmp_path / "my-app"
        assert project_dir.is_dir()
        assert (project_dir / ".agentic-dev").is_dir()
        assert (project_dir / ".agentic-dev" / "artifacts").is_dir()
        assert not (project_dir / "frontend").exists()
        assert not (project_dir / "backend").exists()

    @patch("agentic_dev.cli._run_pipeline")
    def test_saves_initial_state(
        self, mock_run_pipeline, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            app,
            ["new", "my-app", "--path", str(tmp_path)],
            input="Build a todo app\n\n\n",
        )

        assert result.exit_code == 0, result.output
        state_mgr = StateManager(tmp_path / "my-app")
        state = state_mgr.load()
        assert state.project_name == "my-app"
        assert state.phase == PipelinePhase.IDLE

    @patch("agentic_dev.cli._run_pipeline")
    def test_saves_config(
        self, mock_run_pipeline, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            app,
            ["new", "my-app", "--path", str(tmp_path)],
            input="Build a todo app\n\n\n",
        )

        assert result.exit_code == 0, result.output
        config_path = tmp_path / "my-app" / AGENTIC_DEV_METADATA_DIR / CONFIG_FILE
        assert config_path.exists()
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["checkpoint"]["after_design"] is True
        assert data["tracks"]

    @patch("agentic_dev.cli._run_pipeline")
    def test_saves_user_input(
        self, mock_run_pipeline, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            app,
            ["new", "my-app", "--path", str(tmp_path)],
            input="Build a todo app\n\n\n",
        )

        assert result.exit_code == 0, result.output
        user_input_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "user_input.md"
        assert user_input_path.exists()
        assert "todo" in user_input_path.read_text(encoding="utf-8").lower()

    @patch("agentic_dev.cli._run_pipeline")
    def test_calls_pipeline(
        self, mock_run_pipeline, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            app,
            ["new", "my-app", "--path", str(tmp_path)],
            input="Build a todo app\n\n\n",
        )

        assert result.exit_code == 0, result.output
        mock_run_pipeline.assert_called_once()

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.onboarding.analyzer.analyze_codebase")
    def test_from_codebase_runs_analyzer(
        self, mock_analyze, mock_run_pipeline, tmp_path: Path
    ) -> None:
        from agentic_dev.claude.runner import ClaudeResult

        mock_analyze.return_value = ClaudeResult(
            text="# Codebase Analysis\nDetected: Django backend",
            session_id="test",
            cost_usd=0.5,
            exit_code=0,
            raw_json={},
        )
        result = runner.invoke(
            app,
            ["new", "my-app", "--path", str(tmp_path), "--from-codebase", "/some/path"],
            input="Extend this app\n\n\n",
        )

        assert result.exit_code == 0, result.output
        mock_analyze.assert_called_once()
        user_input_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "user_input.md"
        content = user_input_path.read_text(encoding="utf-8")
        assert "Codebase Analysis" in content

    def test_duplicate_project_fails(self, tmp_path: Path) -> None:
        (tmp_path / "my-app").mkdir()
        result = runner.invoke(
            app,
            ["new", "my-app", "--path", str(tmp_path)],
            input="Build something\n\n\n",
        )
        assert result.exit_code == 1

    @patch("agentic_dev.cli._run_pipeline")
    def test_from_file_reads_requirements(
        self, mock_run_pipeline, tmp_path: Path
    ) -> None:
        """--from-file should read requirements from a file instead of interactive input."""
        req_file = tmp_path / "requirements.txt"
        req_file.write_text(
            "Build a comprehensive todo app with tags and filters",
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            ["new", "my-app", "--path", str(tmp_path), "--from-file", str(req_file)],
        )

        assert result.exit_code == 0, result.output
        user_input_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "user_input.md"
        assert user_input_path.exists()
        assert "comprehensive todo app" in user_input_path.read_text(encoding="utf-8").lower()

    def test_from_file_nonexistent_fails(self, tmp_path: Path) -> None:
        """--from-file with a missing file should exit with code 1."""
        result = runner.invoke(
            app,
            ["new", "my-app", "--path", str(tmp_path), "--from-file", "/nonexistent/file.txt"],
        )

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    @patch("agentic_dev.cli._run_pipeline")
    def test_from_file_empty_fails(self, mock_run_pipeline, tmp_path: Path) -> None:
        """--from-file with an empty file should exit with code 1."""
        req_file = tmp_path / "empty.txt"
        req_file.write_text("", encoding="utf-8")

        result = runner.invoke(
            app,
            ["new", "my-app", "--path", str(tmp_path), "--from-file", str(req_file)],
        )

        assert result.exit_code == 1


class TestNewCommandFigma:
    """Tests for --from-figma in the new command."""

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.mcp.setup.check_mcp_prerequisites", return_value=True)
    def test_figma_does_not_concatenate_into_user_input(
        self, mock_mcp, mock_pipeline, tmp_path: Path
    ) -> None:
        """Figma URLs should NOT be appended to user_input."""
        result = runner.invoke(
            app,
            ["new", "my-app", "--path", str(tmp_path),
             "--from-figma", "https://figma.com/file/abc"],
            input="Build a todo app\n\n\n",
        )

        assert result.exit_code == 0, result.output
        user_input_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "user_input.md"
        content = user_input_path.read_text(encoding="utf-8")
        assert "figma.com/file/abc" not in content
        assert "todo" in content.lower()

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.mcp.setup.check_mcp_prerequisites", return_value=True)
    def test_figma_writes_figma_sources_doc(
        self, mock_mcp, mock_pipeline, tmp_path: Path
    ) -> None:
        """--from-figma should persist URLs in figma_sources document."""
        result = runner.invoke(
            app,
            ["new", "my-app", "--path", str(tmp_path),
             "--from-figma", "https://figma.com/file/abc::Main UI"],
            input="Build a todo app\n\n\n",
        )

        assert result.exit_code == 0, result.output
        figma_sources_path = tmp_path / "my-app" / ".agentic-dev" / "artifacts" / "figma_sources.md"
        assert figma_sources_path.exists()
        content = figma_sources_path.read_text(encoding="utf-8")
        assert "https://figma.com/file/abc" in content
        assert "Main UI" in content

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.mcp.setup.check_mcp_prerequisites", return_value=True)
    def test_figma_only_does_not_abort(
        self, mock_mcp, mock_pipeline, tmp_path: Path
    ) -> None:
        """--from-figma alone (no text input) should NOT abort."""
        result = runner.invoke(
            app,
            ["new", "my-app", "--path", str(tmp_path),
             "--from-figma", "https://figma.com/file/abc"],
            input="\n\n",
        )

        assert result.exit_code == 0, result.output
        mock_pipeline.assert_called_once()


class TestStatusCommand:
    def test_displays_state(self, project_with_state: Path) -> None:
        result = runner.invoke(
            app,
            ["status", "test-app", "--path", str(project_with_state)],
        )

        assert result.exit_code == 0, result.output
        assert "test-app" in result.output
        assert "IDLE" in result.output

    def test_missing_project_fails(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["status", "nonexistent", "--path", str(tmp_path)],
        )
        assert result.exit_code == 1

    def test_no_app_name_fails(self) -> None:
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 1


class TestConfigCommand:
    def test_set_autonomy_full(self, project_with_state: Path) -> None:
        result = runner.invoke(
            app,
            ["config", "test-app", "--autonomy", "full", "--path", str(project_with_state)],
        )

        assert result.exit_code == 0, result.output
        assert "after_design: False" in result.output

    def test_set_autonomy_maximum(self, project_with_state: Path) -> None:
        result = runner.invoke(
            app,
            ["config", "test-app", "--autonomy", "maximum", "--path", str(project_with_state)],
        )

        assert result.exit_code == 0, result.output
        assert "after_design: True" in result.output
        assert "after_each_sprint: True" in result.output
        assert "before_uat: True" in result.output

    def test_set_individual_checkpoints(self, project_with_state: Path) -> None:
        result = runner.invoke(
            app,
            [
                "config", "test-app",
                "--checkpoints", "after_design,before_uat",
                "--path", str(project_with_state),
            ],
        )

        assert result.exit_code == 0, result.output
        assert "after_design: True" in result.output
        assert "before_uat: True" in result.output
        assert "after_each_sprint: False" in result.output

    def test_config_persists(self, project_with_state: Path) -> None:
        runner.invoke(
            app,
            ["config", "test-app", "--autonomy", "full", "--path", str(project_with_state)],
        )

        config_path = (
            project_with_state / "test-app" / AGENTIC_DEV_METADATA_DIR / CONFIG_FILE
        )
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["after_design"] is False
        assert data["after_each_sprint"] is False
        assert data["before_uat"] is False

    def test_missing_project_fails(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["config", "nonexistent", "--autonomy", "full", "--path", str(tmp_path)],
        )
        assert result.exit_code == 1


class TestResumeCommand:
    def test_no_app_name_fails(self) -> None:
        result = runner.invoke(app, ["resume"])
        assert result.exit_code == 1

    def test_missing_project_fails(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["resume", "nonexistent", "--path", str(tmp_path)],
        )
        assert result.exit_code == 1

    @patch("agentic_dev.cli._run_pipeline")
    def test_injects_feedback(
        self, mock_run_pipeline, project_with_state: Path
    ) -> None:
        result = runner.invoke(
            app,
            [
                "resume", "test-app",
                "--feedback", "Please add dark mode",
                "--path", str(project_with_state),
            ],
        )

        assert result.exit_code == 0, result.output
        assert "Feedback injected" in result.output

        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        assert state.checkpoint_feedback == "Please add dark mode"

    @patch("agentic_dev.cli._run_pipeline")
    def test_skip_sprint_marks_sprint_complete(
        self, mock_run_pipeline, project_with_state: Path
    ) -> None:
        """--skip-sprint N marks sprint N as complete before resuming."""
        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        state.phase = PipelinePhase.FAILED
        state.failed_at_phase = PipelinePhase.SPRINTING
        state.sprints = [
            SprintState(sprint_number=1, name="Foundation", status=SprintStatus.COMPLETE),
            SprintState(sprint_number=2, name="Auth", status=SprintStatus.COMPLETE),
            SprintState(sprint_number=3, name="Overdue Detection", status=SprintStatus.FAILED),
        ]
        state_mgr.save(state)

        result = runner.invoke(
            app,
            [
                "resume", "test-app",
                "--skip-sprint", "3",
                "--path", str(project_with_state),
            ],
        )

        assert result.exit_code == 0, result.output
        assert "Skipped sprint 3" in result.output

        updated_state = state_mgr.load()
        skipped = next(s for s in updated_state.sprints if s.sprint_number == 3)
        assert skipped.status == SprintStatus.COMPLETE
        assert skipped.completed_at is not None

    @patch("agentic_dev.cli._run_pipeline")
    def test_skip_sprint_invalid_number_fails(
        self, mock_run_pipeline, project_with_state: Path
    ) -> None:
        """--skip-sprint with a non-existent sprint number exits with code 1."""
        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        state.phase = PipelinePhase.FAILED
        state.failed_at_phase = PipelinePhase.SPRINTING
        state.sprints = [
            SprintState(sprint_number=1, name="Foundation", status=SprintStatus.FAILED),
        ]
        state_mgr.save(state)

        result = runner.invoke(
            app,
            [
                "resume", "test-app",
                "--skip-sprint", "99",
                "--path", str(project_with_state),
            ],
        )

        assert result.exit_code == 1


class TestCostCommand:
    def test_no_runs_shows_message(self, project_with_state: Path) -> None:
        result = runner.invoke(
            app,
            ["cost", "test-app", "--path", str(project_with_state)],
        )

        assert result.exit_code == 0
        assert "No agent runs" in result.output

    def test_missing_project_fails(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["cost", "nonexistent", "--path", str(tmp_path)],
        )
        assert result.exit_code == 1


class TestLogsCommand:
    def test_no_logs_shows_message(self, project_with_state: Path) -> None:
        result = runner.invoke(
            app,
            ["logs", "test-app", "--path", str(project_with_state)],
        )

        assert result.exit_code == 0
        assert "No log files" in result.output or "No pipeline runs" in result.output

    def test_displays_pipeline_log(self, project_with_state: Path) -> None:
        logs_dir = (
            project_with_state / "test-app" / AGENTIC_DEV_METADATA_DIR / "logs"
        )
        run_dir = logs_dir / "runs" / "abc123def456"
        run_dir.mkdir(parents=True)
        (run_dir / "pipeline.log").write_text(
            "Log content here", encoding="utf-8"
        )
        latest = logs_dir / "latest"
        latest.symlink_to(run_dir)

        result = runner.invoke(
            app,
            ["logs", "test-app", "--path", str(project_with_state)],
        )

        assert result.exit_code == 0
        assert "Log content here" in result.output

    def test_filter_by_agent(self, project_with_state: Path) -> None:
        logs_dir = (
            project_with_state / "test-app" / AGENTIC_DEV_METADATA_DIR / "logs"
        )
        dumps_dir = logs_dir / "agent_dumps"
        dumps_dir.mkdir(parents=True)
        (dumps_dir / "architect_20260401T143201Z.json").write_text(
            '{"agent": "architect"}', encoding="utf-8"
        )
        (dumps_dir / "frontend_20260401T143201Z.json").write_text(
            '{"agent": "frontend"}', encoding="utf-8"
        )

        result = runner.invoke(
            app,
            ["logs", "test-app", "--agent", "architect", "--path", str(project_with_state)],
        )

        assert result.exit_code == 0
        assert "architect" in result.output
        assert "frontend" not in result.output

    def test_view_specific_run(self, project_with_state: Path) -> None:
        logs_dir = (
            project_with_state / "test-app" / AGENTIC_DEV_METADATA_DIR / "logs"
        )
        run_dir = logs_dir / "runs" / "specific123ab"
        run_dir.mkdir(parents=True)
        (run_dir / "pipeline.log").write_text("specific run log", encoding="utf-8")

        result = runner.invoke(
            app,
            ["logs", "test-app", "--run", "specific123ab", "--path", str(project_with_state)],
        )

        assert result.exit_code == 0
        assert "specific run log" in result.output

    def test_missing_project_fails(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["logs", "nonexistent", "--path", str(tmp_path)],
        )
        assert result.exit_code == 1


class TestUpdateCommand:
    @patch("agentic_dev.cli._collect_user_requirements", return_value="Add dark mode")
    def test_requires_complete_state(self, mock_collect, project_with_state: Path) -> None:
        """Update should fail when project is not in COMPLETE phase."""
        result = runner.invoke(
            app,
            ["update", "test-app", "--path", str(project_with_state)],
        )

        assert result.exit_code == 1
        assert "COMPLETE" in result.output

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.cli._collect_user_requirements", return_value="Add dark mode")
    def test_interactive_input_saves_doc(
        self, mock_collect, mock_run_pipeline, project_with_state: Path
    ) -> None:
        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state_mgr.save(state)

        result = runner.invoke(
            app,
            ["update", "test-app", "--path", str(project_with_state)],
        )

        assert result.exit_code == 0, result.output
        user_input_path = project_with_state / "test-app" / ".agentic-dev" / "artifacts" / "user_input.md"
        assert user_input_path.exists()
        assert "dark mode" in user_input_path.read_text(encoding="utf-8").lower()

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.cli._collect_user_requirements", return_value="Add dark mode")
    def test_update_resets_state(
        self, mock_collect, mock_run_pipeline, project_with_state: Path
    ) -> None:
        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state_mgr.save(state)

        runner.invoke(
            app,
            ["update", "test-app", "--path", str(project_with_state)],
        )

        updated_state = state_mgr.load()
        assert updated_state.mode == "update"
        assert updated_state.phase == PipelinePhase.FEATURE_ANALYSIS

    @patch("agentic_dev.cli._collect_user_requirements", return_value="")
    def test_empty_input_fails(self, mock_collect, project_with_state: Path) -> None:
        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state_mgr.save(state)

        result = runner.invoke(
            app,
            ["update", "test-app", "--path", str(project_with_state)],
        )

        assert result.exit_code == 1

    def test_missing_project_fails(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["update", "nonexistent", "--path", str(tmp_path)],
        )
        assert result.exit_code == 1

    @patch("agentic_dev.cli._run_pipeline")
    def test_from_file_reads_requirements(
        self, mock_run_pipeline, project_with_state: Path
    ) -> None:
        """--from-file should read change description from a file."""
        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state_mgr.save(state)

        req_file = project_with_state / "changes.txt"
        req_file.write_text("Add dark mode support", encoding="utf-8")

        result = runner.invoke(
            app,
            ["update", "test-app", "--from-file", str(req_file), "--path", str(project_with_state)],
        )

        assert result.exit_code == 0, result.output
        user_input_path = project_with_state / "test-app" / ".agentic-dev" / "artifacts" / "user_input.md"
        assert "dark mode" in user_input_path.read_text(encoding="utf-8").lower()

    def test_from_file_nonexistent_fails(self, project_with_state: Path) -> None:
        """--from-file with a missing file should exit with code 1."""
        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state_mgr.save(state)

        result = runner.invoke(
            app,
            ["update", "test-app", "--from-file", "/no/such/file.txt", "--path", str(project_with_state)],
        )

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_from_file_and_full_spec_mutually_exclusive(
        self, project_with_state: Path
    ) -> None:
        """Providing both --from-file and --full-spec should error."""
        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state_mgr.save(state)

        req_file = project_with_state / "changes.txt"
        req_file.write_text("Add dark mode", encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "update", "test-app",
                "--from-file", str(req_file),
                "--full-spec", str(req_file),
                "--path", str(project_with_state),
            ],
        )

        assert result.exit_code == 1
        assert "cannot use both" in result.output.lower()


class TestUpdateCommandFigma:
    """Tests for --from-figma in the update command."""

    def _set_complete(self, project_with_state: Path) -> None:
        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state_mgr.save(state)

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.mcp.setup.check_mcp_prerequisites", return_value=True)
    def test_figma_only_runs_pipeline(
        self, mock_mcp, mock_pipeline, project_with_state: Path
    ) -> None:
        """--from-figma alone should drive an update without text input."""
        self._set_complete(project_with_state)

        result = runner.invoke(
            app,
            ["update", "test-app", "--path", str(project_with_state),
             "--from-figma", "https://figma.com/file/abc"],
        )

        assert result.exit_code == 0, result.output
        mock_pipeline.assert_called_once()

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.mcp.setup.check_mcp_prerequisites", return_value=True)
    def test_figma_writes_figma_sources(
        self, mock_mcp, mock_pipeline, project_with_state: Path
    ) -> None:
        """--from-figma should write figma_sources."""
        self._set_complete(project_with_state)

        runner.invoke(
            app,
            ["update", "test-app", "--path", str(project_with_state),
             "--from-figma", "https://figma.com/file/abc::Main UI"],
        )

        project_dir = project_with_state / "test-app"
        doc_store = DocumentStore(project_dir)
        assert doc_store.exists("figma_sources")
        assert "Main UI" in doc_store.read("figma_sources")

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.mcp.setup.check_mcp_prerequisites", return_value=True)
    def test_figma_only_does_not_write_user_input(
        self, mock_mcp, mock_pipeline, project_with_state: Path
    ) -> None:
        """--from-figma alone should not write user_input or change_request."""
        self._set_complete(project_with_state)

        runner.invoke(
            app,
            ["update", "test-app", "--path", str(project_with_state),
             "--from-figma", "https://figma.com/file/abc"],
        )

        project_dir = project_with_state / "test-app"
        # user_input might exist from archiving, but should not contain new content
        # change_request should not be written
        assert not (project_dir / ".agentic-dev" / "artifacts" / "change_request.md").exists()

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.mcp.setup.check_mcp_prerequisites", return_value=True)
    def test_figma_only_restarts_from_architecture(
        self, mock_mcp, mock_pipeline, project_with_state: Path
    ) -> None:
        """Figma-only update should restart from ARCHITECTURE phase."""
        self._set_complete(project_with_state)

        runner.invoke(
            app,
            ["update", "test-app", "--path", str(project_with_state),
             "--from-figma", "https://figma.com/file/abc"],
        )

        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        assert state.phase == PipelinePhase.ARCHITECTURE

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.cli._collect_user_requirements", return_value="Add dark mode")
    @patch("agentic_dev.mcp.setup.check_mcp_prerequisites", return_value=True)
    def test_figma_with_text_writes_both_channels(
        self, mock_mcp, mock_collect, mock_pipeline,
        project_with_state: Path
    ) -> None:
        """--from-figma with text input writes both text and design channels."""
        self._set_complete(project_with_state)

        runner.invoke(
            app,
            ["update", "test-app", "--path", str(project_with_state),
             "--from-figma", "https://figma.com/file/abc"],
        )

        project_dir = project_with_state / "test-app"
        doc_store = DocumentStore(project_dir)
        assert doc_store.exists("user_input")
        assert "dark mode" in doc_store.read("user_input").lower()
        assert doc_store.exists("figma_sources")

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.mcp.setup.check_mcp_prerequisites", return_value=True)
    def test_figma_with_full_spec_writes_figma_sources(
        self, mock_mcp, mock_pipeline, project_with_state: Path
    ) -> None:
        """--full-spec + --from-figma should write figma_sources."""
        self._set_complete(project_with_state)

        spec_file = project_with_state / "full_spec.txt"
        spec_file.write_text("Complete new spec", encoding="utf-8")

        runner.invoke(
            app,
            ["update", "test-app", "--path", str(project_with_state),
             "--full-spec", str(spec_file),
             "--from-figma", "https://figma.com/file/abc"],
        )

        project_dir = project_with_state / "test-app"
        doc_store = DocumentStore(project_dir)
        assert doc_store.exists("figma_sources")


class TestRemediateCommand:
    def test_requires_complete_state(self, project_with_state: Path) -> None:
        result = runner.invoke(
            app,
            ["remediate", "test-app", "--path", str(project_with_state)],
        )

        assert result.exit_code == 1
        assert "COMPLETE" in result.output

    def test_requires_uat_report(self, project_with_state: Path) -> None:
        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state_mgr.save(state)

        result = runner.invoke(
            app,
            ["remediate", "test-app", "--path", str(project_with_state)],
        )

        assert result.exit_code == 1
        assert "UAT report" in result.output

    @patch("agentic_dev.cli._run_pipeline")
    def test_remediate_resets_and_runs_pipeline(
        self, mock_run_pipeline, project_with_state: Path
    ) -> None:
        project_dir = project_with_state / "test-app"
        state_mgr = StateManager(project_dir)
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state_mgr.save(state)

        doc_store = DocumentStore(project_dir)
        doc_store.write("uat_report", "FAIL: Missing empty state handling.")

        result = runner.invoke(
            app,
            ["remediate", "test-app", "--path", str(project_with_state)],
        )

        assert result.exit_code == 0, result.output
        mock_run_pipeline.assert_called_once()

        updated_state = state_mgr.load()
        assert updated_state.mode == "remediate"
        assert updated_state.phase == PipelinePhase.INPUT_PROCESSING
        assert updated_state.remediation_cycle == 1

    @patch("agentic_dev.cli._run_pipeline")
    def test_remediate_writes_composed_input(
        self, mock_run_pipeline, project_with_state: Path
    ) -> None:
        project_dir = project_with_state / "test-app"
        state_mgr = StateManager(project_dir)
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state_mgr.save(state)

        doc_store = DocumentStore(project_dir)
        doc_store.write("uat_report", "FAIL: Missing confirmation dialog.")

        runner.invoke(
            app,
            ["remediate", "test-app", "--path", str(project_with_state)],
        )

        user_input = doc_store.read("user_input")
        assert "Remediation Request" in user_input
        assert "Missing confirmation dialog" in user_input

    @patch("agentic_dev.cli._run_pipeline")
    def test_remediate_increments_cycle(
        self, mock_run_pipeline, project_with_state: Path
    ) -> None:
        project_dir = project_with_state / "test-app"
        state_mgr = StateManager(project_dir)
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state.remediation_cycle = 2
        state_mgr.save(state)

        doc_store = DocumentStore(project_dir)
        doc_store.write("uat_report", "FAIL: Still broken.")

        runner.invoke(
            app,
            ["remediate", "test-app", "--path", str(project_with_state)],
        )

        updated_state = state_mgr.load()
        assert updated_state.remediation_cycle == 3

    def test_missing_project_fails(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["remediate", "nonexistent", "--path", str(tmp_path)],
        )
        assert result.exit_code == 1

    def test_remediate_help(self) -> None:
        result = runner.invoke(app, ["remediate", "--help"])
        assert result.exit_code == 0
        assert "UAT" in result.output or "remediat" in result.output.lower()
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
