"""Synchronous pre-install of UAT track dependencies.

The environment detector writes ``bootstrap.md`` with a per-track ``Install:``
command. Running those commands once up-front (engine-side) keeps the UAT
agent's turn budget for actual AC verification — otherwise a cold
``yarn install`` or ``docker compose build`` can eat 20+ turns just streaming
log output.

Failures are logged but do not abort UAT; the per-track prereqs probe and the
UAT agent itself will surface the broken state. The aim is best-effort
bootstrap, not strict gating.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agentic_dev.documents.store import DocumentStore
from agentic_dev.onboarding.environment import parse_install_commands
from agentic_dev.tracks import Track

_INSTALL_TIMEOUT_SECONDS = 60 * 15  # 15 minutes per install command


def preinstall_for_uat(
    project_dir: Path,
    run_id: str,
    tracks: list[Track],
    doc_store: DocumentStore,
) -> dict[str, int]:
    """Run each in-scope track's install command and return ``{track: exit_code}``."""
    if not doc_store.exists("bootstrap"):
        return {}

    commands = parse_install_commands(doc_store.read("bootstrap"))
    track_by_name = {t.name: t for t in tracks}

    log_dir = project_dir / ".agentic-dev" / "uat" / run_id
    log_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, int] = {}
    for name, command in commands.items():
        track = track_by_name.get(name)
        if track is None:
            continue
        cwd = project_dir / track.path
        log_path = log_dir / f"install_{name}.log"
        results[name] = _run(command, cwd=cwd, log_path=log_path)
    return results


def _run(command: str, cwd: Path, log_path: Path) -> int:
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"$ {command}\n(cwd: {cwd})\n\n")
        log.flush()
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=_INSTALL_TIMEOUT_SECONDS,
                check=False,
            )
            return completed.returncode
        except subprocess.TimeoutExpired:
            log.write(f"\n[timed out after {_INSTALL_TIMEOUT_SECONDS}s]\n")
            return 124
        except OSError as exc:
            log.write(f"\n[failed to execute: {exc}]\n")
            return 127
