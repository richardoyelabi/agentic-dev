"""Tests for best-effort engine-side UAT teardown."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentic_dev.documents.store import DocumentStore
from agentic_dev.uat.teardown import parse_compose_files, teardown_for_uat


def _make_doc_store(bootstrap_md: str | None) -> MagicMock:
    store = MagicMock(spec=DocumentStore)
    if bootstrap_md is None:
        store.exists.return_value = False
        store.read.side_effect = AssertionError("should not read")
    else:
        store.exists.return_value = True
        store.read.return_value = bootstrap_md
    return store


class TestParseComposeFiles:
    def test_extracts_compose_file(self) -> None:
        bs = "- UAT: `docker compose -f docker-compose.e2e.yml --profile run-tests up`"
        assert parse_compose_files(bs) == ["docker-compose.e2e.yml"]

    def test_dedupes_and_supports_hyphenated(self) -> None:
        bs = (
            "docker compose -f a.yml up\n"
            "docker compose -f a.yml down\n"
            "docker-compose -f b.yml up"
        )
        assert parse_compose_files(bs) == ["a.yml", "b.yml"]

    def test_empty_when_no_compose(self) -> None:
        assert parse_compose_files("cd frontend && yarn dev") == []


class TestTeardownForUat:
    def test_no_op_when_bootstrap_missing(self, tmp_path: Path) -> None:
        store = _make_doc_store(None)
        with patch("agentic_dev.uat.teardown.subprocess.run") as run:
            assert teardown_for_uat(tmp_path, "r1", store) == []
        run.assert_not_called()

    def test_no_op_when_no_compose_stack(self, tmp_path: Path) -> None:
        store = _make_doc_store("cd frontend && yarn dev")
        with patch("agentic_dev.uat.teardown.subprocess.run") as run:
            assert teardown_for_uat(tmp_path, "r1", store) == []
        run.assert_not_called()

    def test_runs_compose_down_and_logs(self, tmp_path: Path) -> None:
        store = _make_doc_store(
            "- UAT: `docker compose -f docker-compose.e2e.yml up --build`"
        )
        with patch(
            "agentic_dev.uat.teardown.subprocess.run",
            return_value=MagicMock(returncode=0),
        ) as run:
            result = teardown_for_uat(tmp_path, "run-1", store)

        assert result == ["docker-compose.e2e.yml"]
        assert (
            "docker compose -f docker-compose.e2e.yml down --remove-orphans"
            in run.call_args.args[0]
        )
        assert run.call_args.kwargs["cwd"] == tmp_path
        log = tmp_path / ".agentic-dev" / "uat" / "run-1" / "teardown.log"
        assert log.exists()
        assert "docker-compose.e2e.yml down" in log.read_text()

    def test_best_effort_swallows_os_error(self, tmp_path: Path) -> None:
        store = _make_doc_store("`docker compose -f x.yml up`")
        with patch(
            "agentic_dev.uat.teardown.subprocess.run",
            side_effect=OSError("docker not found"),
        ):
            assert teardown_for_uat(tmp_path, "r1", store) == ["x.yml"]  # no raise
        log = tmp_path / ".agentic-dev" / "uat" / "r1" / "teardown.log"
        assert "failed to execute" in log.read_text()

    def test_best_effort_swallows_timeout(self, tmp_path: Path) -> None:
        store = _make_doc_store("`docker compose -f x.yml up`")
        with patch(
            "agentic_dev.uat.teardown.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="down", timeout=120),
        ):
            assert teardown_for_uat(tmp_path, "r1", store) == ["x.yml"]  # no raise
        log = tmp_path / ".agentic-dev" / "uat" / "r1" / "teardown.log"
        assert "timed out" in log.read_text()
