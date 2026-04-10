"""Tests for the agentic-dev CLI."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from agentic_dev.cli import app
from agentic_dev.config import AGENTIC_DEV_METADATA_DIR, CONFIG_FILE
from agentic_dev.documents.store import DocumentStore
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
    (project_dir / "docs").mkdir()
    (project_dir / "docs" / "qa_reports").mkdir()
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
        assert "app-name" in result.output.lower() or "APP_NAME" in result.output

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
        assert (project_dir / "docs").is_dir()
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
        assert data["after_design"] is True

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
        user_input_path = tmp_path / "my-app" / "docs" / "user_input.md"
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
        user_input_path = tmp_path / "my-app" / "docs" / "user_input.md"
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
        user_input_path = tmp_path / "my-app" / "docs" / "user_input.md"
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
        user_input_path = project_with_state / "test-app" / "docs" / "user_input.md"
        assert user_input_path.exists()
        assert "dark mode" in user_input_path.read_text(encoding="utf-8").lower()

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.cli._collect_user_requirements", return_value="Add dark mode")
    def test_update_archives_docs(
        self, mock_collect, mock_run_pipeline, project_with_state: Path
    ) -> None:
        project_dir = project_with_state / "test-app"
        state_mgr = StateManager(project_dir)
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state_mgr.save(state)

        # Write a doc that should be archived
        doc_store = DocumentStore(project_dir)
        doc_store.write("features.md", "original features")

        result = runner.invoke(
            app,
            ["update", "test-app", "--path", str(project_with_state)],
        )

        assert result.exit_code == 0, result.output
        archive_dir = project_dir / "docs" / "archive"
        assert archive_dir.exists()
        # At least one archive subdirectory should exist
        assert len(list(archive_dir.iterdir())) >= 1

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

    @patch("agentic_dev.cli._run_pipeline")
    @patch("agentic_dev.cli._collect_user_requirements", return_value="Add dark mode")
    def test_update_from_adopted_state(
        self, mock_collect, mock_run_pipeline, project_with_state: Path
    ) -> None:
        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        state.phase = PipelinePhase.ADOPTED
        state_mgr.save(state)

        result = runner.invoke(
            app,
            ["update", "test-app", "--path", str(project_with_state)],
        )

        assert result.exit_code == 0, result.output
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
        user_input_path = project_with_state / "test-app" / "docs" / "user_input.md"
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
    def test_remediate_archives_docs(
        self, mock_run_pipeline, project_with_state: Path
    ) -> None:
        project_dir = project_with_state / "test-app"
        state_mgr = StateManager(project_dir)
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state_mgr.save(state)

        doc_store = DocumentStore(project_dir)
        doc_store.write("uat_report", "FAIL: Something broke.")
        doc_store.write("features.md", "original features")

        runner.invoke(
            app,
            ["remediate", "test-app", "--path", str(project_with_state)],
        )

        archive_dir = project_dir / "docs" / "archive" / "cycle_0"
        assert archive_dir.exists()
        assert (archive_dir / "features.md").exists()

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


class TestIntegrateCommand:
    def test_integrate_help(self) -> None:
        result = runner.invoke(app, ["integrate", "--help"])
        assert result.exit_code == 0
        assert "integration" in result.output.lower() or "mcp" in result.output.lower()

    def test_integrate_requires_complete_or_adopted(self, project_with_state: Path) -> None:
        """Integrate should fail when project is not in COMPLETE or ADOPTED phase."""
        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        state.phase = PipelinePhase.SPRINTING
        state_mgr.save(state)

        result = runner.invoke(
            app,
            ["integrate", "test-app", "--path", str(project_with_state), "--yes"],
        )

        assert result.exit_code == 1
        assert "COMPLETE" in result.output or "ADOPTED" in result.output

    def test_integrate_no_qualifying_sprints(self, project_with_state: Path) -> None:
        """Integrate should exit when no sprints have integration services."""
        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state.sprints = [
            SprintState(sprint_number=1, name="Sprint 1", status=SprintStatus.COMPLETE),
        ]
        state_mgr.save(state)

        result = runner.invoke(
            app,
            ["integrate", "test-app", "--path", str(project_with_state), "--yes"],
        )

        assert result.exit_code == 1
        assert "no qualifying" in result.output.lower() or "no sprint" in result.output.lower()

    @patch("agentic_dev.cli.check_mcp_prerequisites", return_value=False)
    def test_integrate_blocks_on_mcp_not_ready(
        self, mock_mcp_check, project_with_state: Path,
    ) -> None:
        """Integrate should exit when MCP services are not configured."""
        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state.sprints = [
            SprintState(
                sprint_number=1, name="Sprint 1",
                status=SprintStatus.COMPLETE,
                integration_services=["github"],
            ),
        ]
        state_mgr.save(state)

        result = runner.invoke(
            app,
            ["integrate", "test-app", "--path", str(project_with_state), "--yes"],
        )

        assert result.exit_code == 1
        mock_mcp_check.assert_called_once()

    @patch("agentic_dev.cli.check_mcp_prerequisites", return_value=True)
    @patch("agentic_dev.cli._run_integration")
    def test_integrate_runs_for_qualifying_sprint(
        self, mock_run_integration, mock_mcp_check, project_with_state: Path,
    ) -> None:
        """Integrate should run for sprints with integration services."""
        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state.sprints = [
            SprintState(
                sprint_number=1, name="Sprint 1",
                status=SprintStatus.COMPLETE,
                integration_services=["github"],
            ),
            SprintState(
                sprint_number=2, name="Sprint 2",
                status=SprintStatus.COMPLETE,
            ),
        ]
        state_mgr.save(state)

        result = runner.invoke(
            app,
            ["integrate", "test-app", "--path", str(project_with_state), "--yes"],
        )

        assert result.exit_code == 0, result.output
        mock_run_integration.assert_called_once()

    @patch("agentic_dev.cli.check_mcp_prerequisites", return_value=True)
    @patch("agentic_dev.cli._run_integration")
    def test_integrate_sprint_filter(
        self, mock_run_integration, mock_mcp_check, project_with_state: Path,
    ) -> None:
        """--sprint filters to a specific sprint number."""
        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state.sprints = [
            SprintState(
                sprint_number=1, name="Sprint 1",
                status=SprintStatus.COMPLETE,
                integration_services=["github"],
            ),
            SprintState(
                sprint_number=2, name="Sprint 2",
                status=SprintStatus.COMPLETE,
                integration_services=["stripe"],
            ),
        ]
        state_mgr.save(state)

        result = runner.invoke(
            app,
            [
                "integrate", "test-app",
                "--sprint", "2",
                "--path", str(project_with_state),
                "--yes",
            ],
        )

        assert result.exit_code == 0, result.output
        mock_run_integration.assert_called_once()
        # Verify only sprint 2 was passed
        call_args = mock_run_integration.call_args
        sprints_arg = call_args[1].get("sprints") or call_args[0][2]
        assert len(sprints_arg) == 1
        assert sprints_arg[0].sprint_number == 2

    @patch("agentic_dev.cli.check_mcp_prerequisites", return_value=True)
    @patch("agentic_dev.cli._run_integration")
    def test_integrate_skips_already_integrated(
        self, mock_run_integration, mock_mcp_check, project_with_state: Path,
    ) -> None:
        """Sprints with existing integration_session_id and COMPLETE status are skipped."""
        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state.sprints = [
            SprintState(
                sprint_number=1, name="Sprint 1",
                status=SprintStatus.COMPLETE,
                integration_services=["github"],
                integration_session_id="sess-already-done",
            ),
        ]
        state_mgr.save(state)

        result = runner.invoke(
            app,
            ["integrate", "test-app", "--path", str(project_with_state), "--yes"],
        )

        assert result.exit_code == 1
        assert "no qualifying" in result.output.lower() or "already" in result.output.lower()

    @patch("agentic_dev.cli.check_mcp_prerequisites", return_value=True)
    @patch("agentic_dev.cli._run_integration")
    def test_integrate_force_reruns_completed(
        self, mock_run_integration, mock_mcp_check, project_with_state: Path,
    ) -> None:
        """--force includes sprints that already have integration_session_id."""
        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state.sprints = [
            SprintState(
                sprint_number=1, name="Sprint 1",
                status=SprintStatus.COMPLETE,
                integration_services=["github"],
                integration_session_id="sess-already-done",
            ),
        ]
        state_mgr.save(state)

        result = runner.invoke(
            app,
            [
                "integrate", "test-app",
                "--force",
                "--path", str(project_with_state),
                "--yes",
            ],
        )

        assert result.exit_code == 0, result.output
        mock_run_integration.assert_called_once()

    @patch("agentic_dev.cli.check_mcp_prerequisites", return_value=True)
    @patch("agentic_dev.cli._run_integration")
    def test_integrate_resumes_stuck_sprint(
        self, mock_run_integration, mock_mcp_check, project_with_state: Path,
    ) -> None:
        """Sprints stuck at INTEGRATION_QA are included automatically."""
        state_mgr = StateManager(project_with_state / "test-app")
        state = state_mgr.load()
        state.phase = PipelinePhase.COMPLETE
        state.sprints = [
            SprintState(
                sprint_number=1, name="Sprint 1",
                status=SprintStatus.INTEGRATION_QA,
                integration_services=["github"],
                integration_session_id="sess-crashed",
            ),
        ]
        state_mgr.save(state)

        result = runner.invoke(
            app,
            ["integrate", "test-app", "--path", str(project_with_state), "--yes"],
        )

        assert result.exit_code == 0, result.output
        mock_run_integration.assert_called_once()
