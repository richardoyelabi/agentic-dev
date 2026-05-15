"""UAT-time guard for ``.agentic-dev/secrets.env``.

Runs three checks before the UAT phase dispatches the per-track agents:

1. If no ``env_requirements`` doc exists, do nothing (legacy projects).
2. If ``secrets.env`` is tracked by git (e.g. the managed-gitignore block was
   removed), abort hard — secrets must never enter a commit.
3. If ``secrets.env`` is missing or has ``<FILL ME: ...>`` placeholders, raise
   ``CheckpointPause`` so the engine pauses the pipeline and the CLI tells
   the user which keys to fill before running ``agentic-dev resume``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agentic_dev.config import AGENTIC_DEV_METADATA_DIR
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import CheckpointPause, WorkspaceError
from agentic_dev.onboarding.secrets import parse_secrets_template
from agentic_dev.state.models import PipelinePhase

SECRETS_FILE = f"{AGENTIC_DEV_METADATA_DIR}/secrets.env"


def check_secrets_gate(project_dir: Path, doc_store: DocumentStore) -> None:
    """Raise ``CheckpointPause`` or ``WorkspaceError`` when UAT should not run."""
    if not doc_store.exists("env_requirements"):
        return

    secrets_path = project_dir / SECRETS_FILE
    _assert_gitignored(project_dir, secrets_path)

    state = parse_secrets_template(secrets_path)
    if not secrets_path.exists() or state.has_unfilled_required():
        unfilled = state.unfilled_required or ["(secrets.env not yet created)"]
        listing = "\n  - ".join(unfilled)
        raise CheckpointPause(
            phase=PipelinePhase.UAT,
            message=(
                "Fill the human-required secrets in "
                f"{SECRETS_FILE}, then run `agentic-dev resume`."
                f"\nUnfilled keys:\n  - {listing}"
            ),
        )


def _assert_gitignored(project_dir: Path, secrets_path: Path) -> None:
    """Refuse to proceed if ``secrets.env`` is not gitignored."""
    if not (project_dir / ".git").exists():
        return
    if not secrets_path.exists():
        # Nothing to leak yet — gitignore enforcement applies once file exists.
        return
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(secrets_path)],
        cwd=project_dir,
        capture_output=True,
    )
    if result.returncode != 0:
        raise WorkspaceError(
            f"{SECRETS_FILE} is not gitignored. Refusing to run UAT to avoid "
            "leaking secrets into the repo. Restore the managed gitignore "
            "block or add the path manually before resuming."
        )
