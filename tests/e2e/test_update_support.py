"""End-to-end tests for update support: bug fixes, targeted changes, full re-specification.

These tests make REAL Claude API calls and cost real money (~$5-15 total).
They are skipped by default. Run with:

    E2E=1 pytest tests/e2e/test_update_support.py -v -s --timeout=7200

The tests run in strict order:
  1. Create a simple counter app (pauses at design checkpoint)
  2. Set autonomy to full, then resume to completion
  3. Remediate a bug fix via UAT report
  4. Apply a targeted change via interactive input
  5. Apply a full re-specification via --full-spec
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.e2e.conftest import APP_NAME, PROJECTS_DIR

pytestmark = pytest.mark.e2e

AGENTIC_DEV_BIN = shutil.which("agentic-dev")
if AGENTIC_DEV_BIN is None:
    raise RuntimeError(
        "agentic-dev CLI not found on PATH. Install with: pip install -e ."
    )
AGENTIC_DEV = [AGENTIC_DEV_BIN]

USER_INPUT = (
    "Build a simple counter application.\n"
    "\n"
    "## Features\n"
    "- [F001] Increment/Decrement: Buttons to increase or decrease the counter value\n"
    "- [F002] Display Count: Show the current counter value prominently\n"
    "\n"
    "## Tech Stack\n"
    "- Frontend: React with TypeScript\n"
    "- Backend: FastAPI (Python)\n"
    "\n"
    "## Requirements\n"
    "- The counter value should persist in memory on the backend\n"
    "- REST API endpoints: GET /counter, POST /counter/increment, POST /counter/decrement\n"
    "- Clean, minimal UI\n"
)

FULL_RESPEC_CONTENT = (
    "Build an enhanced counter application.\n"
    "\n"
    "## Features\n"
    "- [F001] Increment/Decrement: Buttons with configurable step size (default 1)\n"
    "- [F002] Display Count: Show current value with a history graph of recent changes\n"
    "- [F003] Reset: A reset button with confirmation dialog that sets counter to zero\n"
    "\n"
    "## Tech Stack\n"
    "- Frontend: React with TypeScript\n"
    "- Backend: FastAPI (Python)\n"
    "\n"
    "## Requirements\n"
    "- The counter value should persist in memory on the backend\n"
    "- REST API endpoints: GET /counter, POST /counter/increment, POST /counter/decrement,\n"
    "  POST /counter/reset\n"
    "- Step size configurable via query parameter (e.g. POST /counter/increment?step=5)\n"
    "- History of last 20 counter changes returned by GET /counter/history\n"
    "- Clean, minimal UI with responsive design\n"
)


def _run_cli(*args: str, input_text: str | None = None, timeout: int = 1800) -> subprocess.CompletedProcess:
    """Run agentic-dev CLI command and return the result."""
    cmd = [*AGENTIC_DEV, *args]
    result = subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result


def _load_state(project_dir: Path) -> dict:
    """Load pipeline state from the project directory."""
    state_path = project_dir / ".agentic-dev" / "state.json"
    return json.loads(state_path.read_text(encoding="utf-8"))


def _artifacts_dir(project_dir: Path) -> Path:
    """All agent-produced docs live under ``<project>/.agentic-dev/artifacts/``."""
    return project_dir / ".agentic-dev" / "artifacts"


def _project_dir() -> Path:
    return PROJECTS_DIR / APP_NAME


class TestUpdateSupportE2E:
    """End-to-end tests exercising the full update lifecycle.

    Tests run sequentially and each depends on the prior step.
    """

    def test_01_create_counter_app(self, projects_dir: Path, app_name: str) -> None:
        """Create the counter app. It will pause at the design checkpoint."""
        project_dir = projects_dir / app_name
        if project_dir.exists():
            pytest.skip(
                f"Project {app_name} already exists at {project_dir}. "
                "Delete it or set E2E_CLEANUP=1 to auto-clean."
            )

        result = _run_cli(
            "new", app_name, "--path", str(projects_dir),
            "--track", "frontend::frontend::web::web",
            "--track", "backend::backend::api::api",
            input_text=USER_INPUT,
            timeout=1800,
        )

        # The new command will run until DESIGN_CHECKPOINT and exit 0
        # (checkpoint pause is handled gracefully, not as an error)
        assert result.returncode == 0, (
            f"Failed to create project:\nstdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
        )
        assert project_dir.is_dir()
        assert (project_dir / ".agentic-dev" / "state.json").exists()
        assert _artifacts_dir(project_dir).is_dir()

        state = _load_state(project_dir)
        # Should be paused at DESIGN_CHECKPOINT (after_design=True by default)
        assert state["phase"] == "DESIGN_CHECKPOINT", (
            f"Expected DESIGN_CHECKPOINT, got {state['phase']}"
        )

    def test_02_set_autonomy_and_resume(self, projects_dir: Path, app_name: str) -> None:
        """Set full autonomy, then resume the pipeline to completion."""
        project_dir = projects_dir / app_name
        if not project_dir.exists():
            pytest.skip("Project not created yet. Run test_01 first.")

        state = _load_state(project_dir)
        if state["phase"] == "COMPLETE":
            pytest.skip("Project already complete.")

        # Set autonomy to full (disables all checkpoints)
        config_result = _run_cli(
            "config", app_name, "--autonomy", "full",
            "--path", str(projects_dir),
        )
        assert config_result.returncode == 0, (
            f"Config failed:\n{config_result.stdout}\n{config_result.stderr}"
        )

        # Resume pipeline to completion
        resume_result = _run_cli(
            "resume", app_name, "--path", str(projects_dir),
            timeout=3600,
        )
        assert resume_result.returncode == 0, (
            f"Resume failed:\nstdout: {resume_result.stdout[-2000:]}\nstderr: {resume_result.stderr[-2000:]}"
        )

        state = _load_state(project_dir)
        assert state["phase"] == "COMPLETE"
        assert state["mode"] == "new"
        assert state["total_cost_usd"] > 0

        # Verify key documents were generated under .agentic-dev/artifacts/
        artifacts = _artifacts_dir(project_dir)
        for doc_name in [
            "structured_input.md", "features.md", "frontend_spec.md",
            "backend_spec.md", "api_contract.md", "sprint_plan.md", "uat_report.md",
        ]:
            assert (artifacts / doc_name).exists(), f"Missing document: {doc_name}"

        # Verify sprints were populated and completed
        assert len(state["sprints"]) > 0
        for sprint in state["sprints"]:
            assert sprint["status"] == "complete", (
                f"Sprint {sprint['sprint_number']} status: {sprint['status']}"
            )

    def test_03_remediate_bug_fix(self, projects_dir: Path, app_name: str) -> None:
        """Run a remediation cycle to fix a bug identified in the UAT report."""
        project_dir = projects_dir / app_name
        if not project_dir.exists():
            pytest.skip("Project not created yet.")

        state = _load_state(project_dir)
        if state["phase"] != "COMPLETE":
            pytest.skip(f"Project not complete (phase={state['phase']}). Run test_02 first.")

        cost_before = state["total_cost_usd"]
        cycle_before = state.get("remediation_cycle", 0)

        # Ensure there's a failing UAT report to remediate.
        # If the real UAT passed, overwrite with a synthetic failure.
        uat_path = _artifacts_dir(project_dir) / "uat_report.md"
        uat_content = uat_path.read_text(encoding="utf-8") if uat_path.exists() else ""
        if "FAIL" not in uat_content.upper():
            uat_path.write_text(
                "# UAT Report\n\n"
                "## Results\n\n"
                "- [F001] Increment/Decrement: **FAIL**\n"
                "  - Counter does not handle rapid successive clicks correctly. "
                "When clicking increment rapidly, some requests are lost and the "
                "displayed count does not match the backend state.\n"
                "  - Decrementing below 0 returns a 500 error instead of clamping to 0.\n\n"
                "- [F002] Display Count: **PASS**\n"
                "  - Current count is displayed correctly when the page loads.\n",
                encoding="utf-8",
            )

        result = _run_cli(
            "remediate", app_name, "--path", str(projects_dir),
            timeout=3600,
        )
        assert result.returncode == 0, (
            f"Remediate failed:\nstdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
        )

        state = _load_state(project_dir)
        assert state["phase"] == "COMPLETE"
        assert state["mode"] == "remediate"
        assert state["remediation_cycle"] == cycle_before + 1

        # Verify remediation input was composed
        user_input_path = _artifacts_dir(project_dir) / "user_input.md"
        user_input = user_input_path.read_text(encoding="utf-8")
        assert "Remediation Request" in user_input

        # Verify cost increased
        assert state["total_cost_usd"] > cost_before

    def test_04_update_targeted_change(self, projects_dir: Path, app_name: str) -> None:
        """Apply a targeted change request to add a reset button."""
        project_dir = projects_dir / app_name
        if not project_dir.exists():
            pytest.skip("Project not created yet.")

        state = _load_state(project_dir)
        if state["phase"] != "COMPLETE":
            pytest.skip(f"Project not complete (phase={state['phase']}). Run test_03 first.")

        cost_before = state["total_cost_usd"]

        result = _run_cli(
            "update", app_name,
            "--path", str(projects_dir),
            input_text="Add a reset button that sets the counter back to zero\n\n",
            timeout=3600,
        )
        assert result.returncode == 0, (
            f"Update failed:\nstdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
        )

        state = _load_state(project_dir)
        assert state["phase"] == "COMPLETE"
        assert state["mode"] == "update"

        # Verify user_input was updated
        user_input_path = _artifacts_dir(project_dir) / "user_input.md"
        user_input = user_input_path.read_text(encoding="utf-8")
        assert "reset" in user_input.lower()

        # Verify cost increased
        assert state["total_cost_usd"] > cost_before

    def test_05_update_full_respec(
        self, projects_dir: Path, app_name: str, tmp_path: Path
    ) -> None:
        """Apply a full re-specification with new features."""
        project_dir = projects_dir / app_name
        if not project_dir.exists():
            pytest.skip("Project not created yet.")

        state = _load_state(project_dir)
        if state["phase"] != "COMPLETE":
            pytest.skip(f"Project not complete (phase={state['phase']}). Run test_04 first.")

        cost_before = state["total_cost_usd"]

        # Write the new spec to a temp file
        spec_file = tmp_path / "e2e_new_spec.txt"
        spec_file.write_text(FULL_RESPEC_CONTENT, encoding="utf-8")

        # The CLI reads ``structured_input.md`` from the artifacts dir for diffing;
        # the pipeline writes it there directly under the track-model refactor.

        result = _run_cli(
            "update", app_name,
            "--full-spec", str(spec_file),
            "--path", str(projects_dir),
            timeout=3600,
        )
        assert result.returncode == 0, (
            f"Full respec failed:\nstdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
        )

        state = _load_state(project_dir)
        assert state["phase"] == "COMPLETE"
        assert state["mode"] == "update"

        # Verify user_input contains the new spec content
        user_input_path = _artifacts_dir(project_dir) / "user_input.md"
        user_input = user_input_path.read_text(encoding="utf-8")
        assert "F003" in user_input
        assert "step size" in user_input.lower()

        # Verify cost increased
        assert state["total_cost_usd"] > cost_before
