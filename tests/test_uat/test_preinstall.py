"""Tests for synchronous pre-install of UAT track dependencies."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

from agentic_dev.documents.store import DocumentStore
from agentic_dev.tracks import Track
from agentic_dev.uat.preinstall import preinstall_for_uat


def _make_doc_store(bootstrap_md: str | None) -> MagicMock:
    store = MagicMock(spec=DocumentStore)
    if bootstrap_md is None:
        store.exists.return_value = False
        store.read.side_effect = AssertionError("should not read")
    else:
        store.exists.return_value = True
        store.read.return_value = bootstrap_md
    return store


def _track(name: str, path: str = ".") -> Track:
    return Track(name=name, path=path, kind="api", uat_kind="api")


class TestPreinstallForUat:
    """``preinstall_for_uat`` runs install commands listed in bootstrap.md."""

    def test_no_op_when_bootstrap_doc_missing(self, tmp_path: Path) -> None:
        store = _make_doc_store(bootstrap_md=None)

        result = preinstall_for_uat(
            project_dir=tmp_path,
            run_id="r1",
            tracks=[_track("backend")],
            doc_store=store,
        )

        assert result == {}

    def test_runs_install_command_and_logs(self, tmp_path: Path) -> None:
        bootstrap = (
            "## backend\n"
            f"- Install: `{sys.executable} -c \"print('hi'); import sys; sys.exit(0)\"`\n"
        )
        store = _make_doc_store(bootstrap_md=bootstrap)
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()

        result = preinstall_for_uat(
            project_dir=tmp_path,
            run_id="r1",
            tracks=[_track("backend", path="backend")],
            doc_store=store,
        )

        assert result == {"backend": 0}
        log = tmp_path / ".agentic-dev" / "uat" / "r1" / "install_backend.log"
        assert log.exists()
        assert "hi" in log.read_text()

    def test_skips_tracks_without_install_command(self, tmp_path: Path) -> None:
        bootstrap = "## backend\n- Run: `uvicorn x`\n"
        store = _make_doc_store(bootstrap_md=bootstrap)

        result = preinstall_for_uat(
            project_dir=tmp_path,
            run_id="r1",
            tracks=[_track("backend")],
            doc_store=store,
        )

        assert result == {}

    def test_install_failure_records_exit_code_does_not_raise(
        self, tmp_path: Path
    ) -> None:
        bootstrap = (
            "## backend\n"
            f"- Install: `{sys.executable} -c \"import sys; sys.exit(7)\"`\n"
        )
        store = _make_doc_store(bootstrap_md=bootstrap)

        result = preinstall_for_uat(
            project_dir=tmp_path,
            run_id="r1",
            tracks=[_track("backend")],
            doc_store=store,
        )

        assert result == {"backend": 7}

    def test_only_runs_for_in_scope_tracks(self, tmp_path: Path) -> None:
        """A bootstrap entry for ``frontend`` is ignored if only backend is in scope."""
        bootstrap = (
            "## backend\n"
            f"- Install: `{sys.executable} -c 'print(1)'`\n\n"
            "## frontend\n"
            f"- Install: `{sys.executable} -c \"import sys; sys.exit(99)\"`\n"
        )
        store = _make_doc_store(bootstrap_md=bootstrap)

        result = preinstall_for_uat(
            project_dir=tmp_path,
            run_id="r1",
            tracks=[_track("backend")],
            doc_store=store,
        )

        assert result == {"backend": 0}
