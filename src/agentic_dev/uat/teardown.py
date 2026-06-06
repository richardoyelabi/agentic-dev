"""Best-effort engine-side teardown of UAT runtime stacks.

UAT agents boot the app to drive it — preferring the docker-compose e2e stack
declared in ``bootstrap.md``. If an agent dies mid-run (e.g. a transient API
timeout) before reaching its own teardown step, that stack is left running and
leaks between runs. This module tears down any compose stack named in
``bootstrap.md`` after UAT finishes — pass, fail, or agent error.

Best-effort: failures (including docker being absent) are logged, never raised;
non-compose host servers rely on the agent's own teardown.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from agentic_dev.documents.store import DocumentStore

_TEARDOWN_TIMEOUT_SECONDS = 120

# Matches ``docker compose -f <file>`` and the older ``docker-compose -f <file>``.
_COMPOSE_FILE_RE = re.compile(r"docker[\s-]compose\s+-f\s+([^\s`]+)")


def parse_compose_files(bootstrap_md: str) -> list[str]:
    """Return the unique ``docker compose -f <file>`` paths in a bootstrap md."""
    seen: list[str] = []
    for match in _COMPOSE_FILE_RE.finditer(bootstrap_md):
        path = match.group(1).strip().strip("`")
        if path and path not in seen:
            seen.append(path)
    return seen


def teardown_for_uat(
    project_dir: Path,
    run_id: str,
    doc_store: DocumentStore,
) -> list[str]:
    """Tear down each compose stack declared in ``bootstrap.md``.

    Returns the list of compose files torn down. No-op when ``bootstrap.md`` is
    absent or names no compose stack. Best-effort — never raises.
    """
    if not doc_store.exists("bootstrap"):
        return []
    compose_files = parse_compose_files(doc_store.read("bootstrap"))
    if not compose_files:
        return []

    log_dir = project_dir / ".agentic-dev" / "uat" / run_id
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "teardown.log"

    with log_path.open("a", encoding="utf-8") as log:
        for compose_file in compose_files:
            command = f"docker compose -f {compose_file} down --remove-orphans"
            log.write(f"$ {command}\n(cwd: {project_dir})\n")
            log.flush()
            try:
                completed = subprocess.run(
                    command,
                    shell=True,
                    cwd=project_dir,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=_TEARDOWN_TIMEOUT_SECONDS,
                    check=False,
                )
                log.write(f"\n[exit {completed.returncode}]\n\n")
            except subprocess.TimeoutExpired:
                log.write(f"\n[timed out after {_TEARDOWN_TIMEOUT_SECONDS}s]\n\n")
            except OSError as exc:
                log.write(f"\n[failed to execute: {exc}]\n\n")
            log.flush()
    return compose_files
