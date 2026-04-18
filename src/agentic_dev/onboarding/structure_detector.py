"""Structure detection for existing projects.

Uses a Claude agent to scan project directories and detect
the frontend/backend directory mapping and the frontend kind.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agentic_dev.claude.runner import ClaudeRunner
from agentic_dev.config import DirectoryMap
from agentic_dev.exceptions import AgenticDevError
from agentic_dev.logging import get_event_logger, emit
from agentic_dev.logging.events import StructureDetectionEvent
from agentic_dev.orchestrator.agent_bridge import AgentRunConfig
from agentic_dev.state.models import FrontendKind

_event_log = get_event_logger("structure_detector")


DETECTOR_PROMPT = """\
You are an expert at analyzing project directory structures. Your job is to scan \
an existing codebase and determine where the frontend and backend code lives and \
what kind of user-facing surface it delivers.

Examine the project in your current working directory. Detect using these markers:

**Directory / project type markers:**
- Frontend markers: package.json with React/Vue/Angular/Svelte/Next.js, tsconfig.json, \
vite.config, webpack.config
- Backend markers: requirements.txt/pyproject.toml with Django/Flask/FastAPI, go.mod, \
Cargo.toml, package.json with Express/Nest/Hono
- Monorepo markers: workspaces in package.json, apps/ or packages/ directories

**Frontend kind markers** (coarse-grained, choose one):
- `mobile` — pubspec.yaml (Flutter); package.json with react-native or expo
- `desktop` — package.json with electron; src-tauri/Cargo.toml or @tauri-apps/* deps
- `web` — package.json with next/vite/react/vue/svelte/solid and no mobile/desktop markers
- `cli` — pyproject.toml/setup.py with [project.scripts] and no web framework; \
go.mod with a single `main` package and no http/gin/echo import
- `none` — only valid when there is no frontend directory at all (pure backend/library)

When the frontend directory exists but no marker matches, fall back to `web`.

Output a single JSON object with exactly these keys:

{"frontend": "<relative path or null>", "backend": "<relative path or null>", \
"project_type": "<fullstack | frontend_only | backend_only>", \
"frontend_kind": "<web | cli | desktop | mobile | none>"}

Paths are relative to the project root. Use "." if code is at the root. \
Output ONLY the JSON object, no other text.
"""


class StructureDetectionError(AgenticDevError):
    """Raised when structure detection fails to produce valid output."""


@dataclass
class DetectionResult:
    """Combined detection output: directory layout plus frontend kind."""

    directory_map: DirectoryMap
    frontend_kind: FrontendKind


async def detect_structure(
    claude: ClaudeRunner,
    project_path: Path,
) -> DetectionResult:
    """Detect the directory structure + frontend kind of an existing project.

    Args:
        claude: The ClaudeRunner instance.
        project_path: Path to the project root.

    Returns:
        DetectionResult with detected DirectoryMap and FrontendKind.

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

    detection = _parse_detection_result(result.text)
    directory_map = detection.directory_map

    project_type = "fullstack"
    if directory_map.frontend and not directory_map.backend:
        project_type = "frontend_only"
    elif directory_map.backend and not directory_map.frontend:
        project_type = "backend_only"

    emit(_event_log, StructureDetectionEvent(
        frontend=directory_map.frontend,
        backend=directory_map.backend,
        project_type=project_type,
        message=(
            f"Structure detected: frontend={directory_map.frontend}, "
            f"backend={directory_map.backend}, "
            f"frontend_kind={detection.frontend_kind.value}"
        ),
    ))

    return detection


def _parse_detection_result(text: str) -> DetectionResult:
    """Extract a DetectionResult from the agent's JSON output."""
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

    directory_map = DirectoryMap(
        frontend=data.get("frontend"),
        backend=data.get("backend"),
    )

    kind_raw = (data.get("frontend_kind") or "").strip().lower()
    try:
        frontend_kind = FrontendKind(kind_raw) if kind_raw else (
            FrontendKind.WEB if directory_map.frontend else FrontendKind.NONE
        )
    except ValueError:
        frontend_kind = (
            FrontendKind.WEB if directory_map.frontend else FrontendKind.NONE
        )

    return DetectionResult(directory_map=directory_map, frontend_kind=frontend_kind)
