"""Discovery agent: a Claude agent that reads a project and emits track JSON."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from agentic_dev.claude.runner import ClaudeRunner
from agentic_dev.exceptions import AgenticDevError
from agentic_dev.orchestrator.agent_bridge import AgentRunConfig
from agentic_dev.tracks import Track


DISCOVERY_PROMPT = """\
You are a project-structure analyst. Inspect the codebase in your current \
working directory and identify the discrete sub-projects ("tracks") it \
contains.

A track is one codebase with a coherent build/run/test loop — for example a \
Python API in `backend/`, a Next.js app in `frontend/`, a worker in \
`workers/`, or a single-codebase project at the repository root.

Heuristics:
- A directory with `pyproject.toml` plus FastAPI / Django / Flask deps \
  → kind=api, uat_kind=api
- A directory with `pyproject.toml` plus Celery / RQ / Dramatiq deps \
  → kind=worker (uat_kind=null)
- A directory with `package.json` plus next.config.* / vite.config.* / \
  react / vue dependencies → kind=web, uat_kind=web
- A directory with `Cargo.toml` or `go.mod` and a binary target \
  → kind=cli (uat_kind=cli)
- A repository with no nested codebase boundaries → ONE track at "." with \
  the appropriate kind
- A directory with a Dockerfile but no clearer signal → inspect the \
  contents and pick the closest kind

Set ``uat_kind`` to the same value as ``kind`` when a runtime UAT is \
feasible (`web`, `api`, `cli`); leave it null otherwise.

Output strict JSON only — no commentary, no prose, no markdown fence:

{
  "tracks": [
    {"name": "<slug matching [a-z0-9_-]+>", "path": "<relative path or '.'>", \
"kind": "<kind>", "uat_kind": "<kind or null>"}
  ],
  "reasoning": "<one or two sentences explaining the choice>"
}
"""


@dataclass(frozen=True)
class DiscoveryResult:
    """Outcome of running the discovery agent."""

    tracks: list[Track]
    reasoning: str
    raw_response: str = field(repr=False)


_JSON_OBJECT_RE = re.compile(r"\{.*\}", flags=re.DOTALL)


def parse_discovery_response(text: str) -> DiscoveryResult:
    """Parse the discovery agent's JSON response into a ``DiscoveryResult``.

    The agent is told to emit strict JSON, but real-world LLM output
    occasionally wraps the payload in a markdown fence or trailing prose.
    This helper extracts the first JSON object found in the response and
    validates each track via ``Track.model_validate``.
    """
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        raise AgenticDevError(
            "Discovery agent returned no JSON object: "
            + (text[:200] if text else "<empty>")
        )

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise AgenticDevError(f"Discovery agent emitted invalid JSON: {exc}") from exc

    raw_tracks = data.get("tracks") if isinstance(data, dict) else None
    if not isinstance(raw_tracks, list) or not raw_tracks:
        raise AgenticDevError(
            "Discovery agent emitted no tracks. Got: " + json.dumps(data)[:200]
        )

    tracks = [Track.model_validate(item) for item in raw_tracks]
    reasoning = str(data.get("reasoning", "")).strip() if isinstance(data, dict) else ""
    return DiscoveryResult(tracks=tracks, reasoning=reasoning, raw_response=text)


async def discover_tracks(
    claude: ClaudeRunner, project_root: Path
) -> DiscoveryResult:
    """Run the discovery Claude agent against ``project_root``."""
    config = AgentRunConfig(
        name="project_discovery",
        model="sonnet",
        permission_mode="plan",
        allowed_tools=["Read", "Glob", "Grep"],
        max_turns=30,
        use_bare_mode=True,
        mcp_config=None,
        system_prompt=None,
    )
    result = await claude.run(
        agent=config,
        prompt=DISCOVERY_PROMPT,
        working_dir=project_root,
    )
    return parse_discovery_response(result.text)
