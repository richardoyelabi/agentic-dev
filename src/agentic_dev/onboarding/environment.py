"""Environment detector — discovers bootstrap commands and required env vars.

Mirrors ``analyzer.py`` in shape but produces three cross-track artifacts in
one LLM call: a bootstrap markdown describing canonical install/run/test/UAT
commands per track, an env-requirements markdown classifying each variable
as auto-fillable / mock-available / human-required, and a ``secrets.env``
template with auto and mock values pre-filled and human-required values as
``<FILL ME: hint>`` placeholders.

The detector runs in bare mode with ``Read``/``Glob``/``Grep`` only — it
does not install dependencies or execute project code. Synchronous
pre-installation is the engine's responsibility (see ``_run_uat``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agentic_dev.claude.runner import ClaudeRunner
from agentic_dev.orchestrator.agent_bridge import AgentRunConfig
from agentic_dev.tracks import Track


ENVIRONMENT_DETECTOR_PROMPT = """\
You are an expert build and runtime engineer onboarding an existing project \
into agentic-dev's automation pipeline. Your job is to discover how the \
project is installed, run, tested, and verified end-to-end so downstream UAT \
agents can use canonical commands instead of improvising.

Inspect the project at your current working directory. Read root-level files \
(`Makefile`, `docker-compose*.yml`, root `package.json`, `pyproject.toml`, \
README, `.github/workflows/*`) and the per-track sources listed below. Look \
for `.env*` files, mock services (e.g. `e2e/mocks/`), and entrypoint \
scripts.

Emit your output as three fenced sections, with NO other text outside the \
fences. Use this exact format:

<<<BOOTSTRAP_MD>>>
# Bootstrap

For each track, list the canonical commands in preference order: docker \
compose > Makefile target > package.json script > raw command. Include:
- Install: <command>
- Run: <command>
- Test: <command>
- UAT (end-to-end): <command, prefer docker compose if a *.e2e.yml exists>

Add a `## Root` section if the project has root-level orchestration (a root \
Makefile, a docker-compose at the root, etc.) that supersedes per-track \
commands.
<<<END_BOOTSTRAP_MD>>>

<<<ENV_REQUIREMENTS_MD>>>
# Env requirements

List every environment variable the project reads (settings modules, \
`os.environ`, `process.env`, `.env*` files). Classify each as:
- **auto**: deterministic safe default exists (random crypto keys, \
  `localhost` URLs, debug flags)
- **mock**: a mock service shipped in the repo provides the value (give the \
  path to the mock and the URL/value to point at)
- **human**: requires a real credential the user must obtain (OAuth client \
  IDs, paid API keys, cloud-vendor credentials)

Group by track.
<<<END_ENV_REQUIREMENTS_MD>>>

<<<SECRETS_ENV>>>
# Pre-fill **auto** values with safe deterministic defaults (e.g. \
# `python -c 'import secrets; print(secrets.token_hex(32))'`).
# Pre-fill **mock** values with the mock service URL or known constant.
# Write **human** values as `KEY=<FILL ME: short hint where to get it>`.
# One KEY=value per line. Comments allowed.
<<<END_SECRETS_ENV>>>
"""


@dataclass(frozen=True)
class EnvironmentReport:
    bootstrap_md: str
    env_requirements_md: str
    secrets_env_template: str


_TRACK_HEADER_RE = re.compile(r"^##\s+(.+?)\s*$")
_INSTALL_LINE_RE = re.compile(r"^\s*[-*]\s*Install:\s*(.+?)\s*$")


def parse_install_commands(bootstrap_md: str) -> dict[str, str]:
    """Extract ``{track_name: install_command}`` from a bootstrap markdown.

    The detector emits sections like ``## backend`` followed by ``- Install: <cmd>``
    lines. Tracks without an ``Install:`` line are omitted. Command strings have
    surrounding backticks stripped so they can be passed straight to a shell.
    """
    commands: dict[str, str] = {}
    current: str | None = None
    for line in bootstrap_md.splitlines():
        header = _TRACK_HEADER_RE.match(line)
        if header:
            current = header.group(1).strip()
            continue
        if current is None:
            continue
        install = _INSTALL_LINE_RE.match(line)
        if install and current not in commands:
            commands[current] = install.group(1).strip().strip("`")
    return commands


_SECTION_RE = {
    "bootstrap_md": re.compile(
        r"<<<BOOTSTRAP_MD>>>(.*?)<<<END_BOOTSTRAP_MD>>>", re.DOTALL
    ),
    "env_requirements_md": re.compile(
        r"<<<ENV_REQUIREMENTS_MD>>>(.*?)<<<END_ENV_REQUIREMENTS_MD>>>",
        re.DOTALL,
    ),
    "secrets_env_template": re.compile(
        r"<<<SECRETS_ENV>>>(.*?)<<<END_SECRETS_ENV>>>", re.DOTALL
    ),
}


def parse_environment_response(text: str) -> EnvironmentReport:
    """Extract the three fenced sections, raising ``ValueError`` on missing."""
    extracted: dict[str, str] = {}
    for field_name, pattern in _SECTION_RE.items():
        match = pattern.search(text)
        if match is None:
            fence = (
                "BOOTSTRAP_MD"
                if field_name == "bootstrap_md"
                else "ENV_REQUIREMENTS_MD"
                if field_name == "env_requirements_md"
                else "SECRETS_ENV"
            )
            raise ValueError(
                f"Environment detector response missing <<<{fence}>>> section"
            )
        extracted[field_name] = match.group(1).strip()
    return EnvironmentReport(**extracted)


async def detect_environment(
    claude: ClaudeRunner,
    project_root: Path,
    tracks: list[Track],
) -> EnvironmentReport:
    """Run the environment-detector agent and parse its response."""
    config = AgentRunConfig(
        name="environment_detector",
        model="sonnet",
        permission_mode="plan",
        allowed_tools=["Read", "Glob", "Grep"],
        max_turns=30,
        use_bare_mode=True,
        mcp_config=None,
        system_prompt=None,
    )

    track_summary = _format_tracks(tracks)
    prompt = f"Tracks in this project:\n{track_summary}\n\n{ENVIRONMENT_DETECTOR_PROMPT}"

    result = await claude.run(
        agent=config,
        prompt=prompt,
        working_dir=project_root,
    )
    return parse_environment_response(result.text)


def _format_tracks(tracks: list[Track]) -> str:
    if not tracks:
        return "(no tracks detected — treat the whole project as a single track)"
    return "\n".join(
        f"- name={t.name}, path={t.path}, kind={t.kind}, uat_kind={t.uat_kind or 'none'}"
        for t in tracks
    )
