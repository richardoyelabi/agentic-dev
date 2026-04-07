"""Structure detection for existing projects.

Uses a Claude agent to scan project directories and detect
the frontend/backend directory mapping.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentic_dev.claude.runner import ClaudeRunner
from agentic_dev.config import DirectoryMap
from agentic_dev.exceptions import AgenticDevError
from agentic_dev.orchestrator.agent_bridge import AgentRunConfig


DETECTOR_PROMPT = """\
You are an expert at analyzing project directory structures. Your job is to scan \
an existing codebase and determine where the frontend and backend code lives.

Examine the project in your current working directory. Look for framework markers:

- Frontend markers: package.json with React/Vue/Angular/Svelte/Next.js, tsconfig.json, \
vite.config, webpack.config
- Backend markers: requirements.txt/pyproject.toml with Django/Flask/FastAPI, go.mod, \
Cargo.toml, package.json with Express/Nest/Hono
- Monorepo markers: workspaces in package.json, apps/ or packages/ directories

Output a single JSON object with exactly these keys:

{"frontend": "<relative path or null>", "backend": "<relative path or null>", \
"project_type": "<fullstack | frontend_only | backend_only>"}

Paths are relative to the project root. Use "." if code is at the root. \
Output ONLY the JSON object, no other text.
"""


class StructureDetectionError(AgenticDevError):
    """Raised when structure detection fails to produce valid output."""


async def detect_structure(
    claude: ClaudeRunner,
    project_path: Path,
) -> DirectoryMap:
    """Detect the directory structure of an existing project.

    Args:
        claude: The ClaudeRunner instance.
        project_path: Path to the project root.

    Returns:
        DirectoryMap with detected frontend/backend paths.

    Raises:
        StructureDetectionError: If the agent output cannot be parsed.
    """
    config = AgentRunConfig(
        name="structure_detector",
        model="sonnet",
        permission_mode="plan",
        allowed_tools=["Read", "Glob", "Grep"],
        max_turns=15,
        use_bare_mode=True,
        mcp_config=None,
        system_prompt=None,
    )

    result = await claude.run(
        agent=config,
        prompt=DETECTOR_PROMPT,
        working_dir=project_path,
    )

    return _parse_detection_result(result.text)


def _parse_detection_result(text: str) -> DirectoryMap:
    """Extract a DirectoryMap from the agent's JSON output."""
    # Find JSON in the output (agent may include some preamble)
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise StructureDetectionError(
            f"Structure detection did not produce valid JSON: {text[:200]}"
        )

    try:
        data = json.loads(text[start:end])
    except json.JSONDecodeError as exc:
        raise StructureDetectionError(
            f"Failed to parse structure detection JSON: {exc}"
        ) from exc

    return DirectoryMap(
        frontend=data.get("frontend"),
        backend=data.get("backend"),
    )
