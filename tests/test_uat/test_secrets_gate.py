"""Tests for the UAT secrets-gate helper."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import CheckpointPause, WorkspaceError
from agentic_dev.state.models import PipelinePhase
from agentic_dev.uat.secrets_gate import (
    SECRETS_FILE,
    check_secrets_gate,
)


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test"], cwd=path, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "test"], cwd=path, check=True
    )


def _make_doc_store(env_requirements: str | None) -> MagicMock:
    store = MagicMock(spec=DocumentStore)
    if env_requirements is None:
        store.exists.return_value = False
        store.read.side_effect = AssertionError("should not read")
    else:
        store.exists.return_value = True
        store.read.return_value = env_requirements
    return store


class TestCheckSecretsGate:
    """``check_secrets_gate`` guards UAT against unfilled secrets and leaks."""

    def test_no_op_when_env_requirements_doc_missing(
        self, tmp_path: Path
    ) -> None:
        """Backwards compatibility: projects onboarded before the env detector."""
        _git_init(tmp_path)
        store = _make_doc_store(env_requirements=None)

        check_secrets_gate(tmp_path, store)

    def test_pauses_when_secrets_env_has_unfilled_placeholder(
        self, tmp_path: Path
    ) -> None:
        _git_init(tmp_path)
        (tmp_path / ".gitignore").write_text(f"{SECRETS_FILE}\n")
        meta = tmp_path / ".agentic-dev"
        meta.mkdir()
        (meta / "secrets.env").write_text(
            "AGORA_APP_ID=<FILL ME: console>\n"
        )
        store = _make_doc_store(env_requirements="# Env\n- AGORA_APP_ID (human)\n")

        with pytest.raises(CheckpointPause) as excinfo:
            check_secrets_gate(tmp_path, store)
        assert excinfo.value.phase == PipelinePhase.UAT
        assert "AGORA_APP_ID" in str(excinfo.value)

    def test_proceeds_when_all_secrets_filled(self, tmp_path: Path) -> None:
        _git_init(tmp_path)
        (tmp_path / ".gitignore").write_text(f"{SECRETS_FILE}\n")
        meta = tmp_path / ".agentic-dev"
        meta.mkdir()
        (meta / "secrets.env").write_text("AGORA_APP_ID=abc123\n")
        store = _make_doc_store(env_requirements="# Env\n- AGORA_APP_ID (human)\n")

        check_secrets_gate(tmp_path, store)

    def test_pauses_when_secrets_file_missing_but_required(
        self, tmp_path: Path
    ) -> None:
        """If env requirements exist but no secrets.env, treat as unfilled."""
        _git_init(tmp_path)
        (tmp_path / ".gitignore").write_text(f"{SECRETS_FILE}\n")
        (tmp_path / ".agentic-dev").mkdir()
        store = _make_doc_store(env_requirements="# Env\n- AGORA_APP_ID (human)\n")

        with pytest.raises(CheckpointPause):
            check_secrets_gate(tmp_path, store)

    def test_aborts_when_secrets_env_is_tracked_by_git(
        self, tmp_path: Path
    ) -> None:
        """Defensive: if managed-gitignore block was removed, refuse to run."""
        _git_init(tmp_path)
        # No .gitignore = secrets.env not ignored.
        meta = tmp_path / ".agentic-dev"
        meta.mkdir()
        secrets = meta / "secrets.env"
        secrets.write_text("AGORA_APP_ID=abc\n")
        # Stage the file so git tracks it.
        subprocess.run(
            ["git", "add", "-f", str(secrets)], cwd=tmp_path, check=True
        )
        store = _make_doc_store(env_requirements="# Env\n- AGORA_APP_ID\n")

        with pytest.raises(WorkspaceError, match="gitignored"):
            check_secrets_gate(tmp_path, store)
