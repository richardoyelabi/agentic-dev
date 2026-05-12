"""Generator for per-track CLAUDE.md files.

Each track gets a CLAUDE.md tailored to its ``kind`` (web / api / cli /
desktop / mobile / worker / library / generic) so Claude Code working
inside a track's directory has track-appropriate conventions to follow.
"""

import re
from pathlib import Path

from agentic_dev.tracks import Track


_DEFAULTS_BY_KIND: dict[str, dict[str, str]] = {
    "web": {
        "framework": "React",
        "styling": "Tailwind CSS",
        "state_management": "TanStack Query",
        "testing": "Vitest + React Testing Library",
    },
    "api": {
        "framework": "Django REST Framework",
        "database": "PostgreSQL",
        "testing": "Pytest",
    },
    "cli": {
        "language": "Python 3.12",
        "testing": "Pytest",
    },
    "desktop": {
        "framework": "Electron",
        "testing": "Vitest",
    },
    "mobile": {
        "framework": "React Native",
        "testing": "Jest + React Native Testing Library",
    },
    "worker": {
        "language": "Python 3.12",
        "queue": "Celery",
        "testing": "Pytest",
    },
    "library": {
        "language": "Python 3.12",
        "testing": "Pytest",
    },
    "generic": {
        "testing": "Pytest",
    },
}


_COMMON_SUPERPOWERS = (
    "## Superpowers\n"
    "- Always practise test-driven development\n"
    "- Use the brainstorming skill before adding new components\n"
    "- Use systematic debugging when tests fail\n"
    "- Run verification before claiming work is complete\n"
)


def _stack_lines(tech_stack: dict[str, str]) -> str:
    lines = []
    for key, value in tech_stack.items():
        label = key.replace("_", " ").title()
        lines.append(f"- **{label}:** {value}")
    return "\n".join(lines) if lines else "- (define in track spec)"


def generate_track_claude_md(
    project_name: str, track: Track, tech_stack: dict[str, str],
) -> str:
    """Generate CLAUDE.md content for a single track's directory.

    The kind-specific defaults fill any gaps left by ``tech_stack`` so that
    Claude Code has a working starting point even before the track's spec
    has been parsed.
    """
    defaults = _DEFAULTS_BY_KIND.get(track.kind, _DEFAULTS_BY_KIND["generic"])
    merged = {**defaults, **tech_stack}
    kind = track.kind

    header = (
        f"# {project_name} — Track: {track.name} ({kind})\n\n"
        "## Tech Stack\n"
        f"{_stack_lines(merged)}\n"
    )

    body_blocks: list[str] = []
    body_blocks.append(
        "## Coding Conventions\n"
        "- Use double quotes for strings\n"
        "- Keep modules small and focused on a single responsibility\n"
        "- Co-locate tests next to the files they test\n"
    )
    body_blocks.append(
        "## Testing\n"
        f"- Framework: {merged.get('testing', 'project default')}\n"
        "- Always practise test-driven development\n"
        "- Write tests before implementing features\n"
    )

    if kind in ("web", "desktop", "mobile"):
        body_blocks.append(
            "## API Layer\n"
            "- All cross-track API interactions must conform to the api_contract artifact\n"
            "- Use a shared API client module for all server requests\n"
            "- Never hardcode endpoint URLs — reference the API Contract for paths and schemas\n"
        )
        body_blocks.append(
            "## Error Handling\n"
            "- Handle all error states graciously with user-friendly messages\n"
            "- Never display raw error objects, stack traces, or technical details to users\n"
            "- Use error boundaries to prevent full-app crashes\n"
            "- Every loading state must have a corresponding error state\n"
        )
    elif kind == "api":
        body_blocks.append(
            "## API Layer\n"
            "- All endpoints must conform to the api_contract artifact\n"
            "- The API Contract is the single source of truth for request/response schemas\n"
            "- Never deviate from the contracted endpoint paths or payload shapes\n"
        )
        body_blocks.append(
            "## Error Handling\n"
            "- Use a consistent error response schema across all endpoints\n"
            "- Never expose internal error details in API responses\n"
            "- Validate all inputs at the boundary with clear error messages\n"
            "- Handle expected failure cases explicitly with appropriate HTTP status codes\n"
        )
    elif kind == "cli":
        body_blocks.append(
            "## CLI Conventions\n"
            "- Errors go to stderr; data goes to stdout\n"
            "- Non-zero exit code on failure; document each distinct exit code\n"
            "- Every command supports a non-interactive mode (no prompts)\n"
        )
    elif kind == "worker":
        body_blocks.append(
            "## Worker Conventions\n"
            "- Every job is idempotent; retries must be safe\n"
            "- Use the queue's primitives for visibility timeouts and dead-letter routing\n"
            "- Emit structured logs and metrics for every job\n"
        )

    return header + "\n" + "\n".join(body_blocks) + "\n" + _COMMON_SUPERPOWERS


def parse_tech_stack(spec_text: str) -> dict[str, str]:
    """Extract tech stack key-value pairs from a track spec.

    Looks for a ``## Tech Stack`` section with bold-labeled bullet lines
    like ``- **Framework:** Next.js`` and returns them as a dict with
    lower-cased, underscored keys (e.g. ``{"framework": "Next.js"}``).
    """
    tech_stack: dict[str, str] = {}

    in_section = False
    for line in spec_text.splitlines():
        stripped = line.strip()

        if re.match(r"^##\s+Tech\s+Stack", stripped, re.IGNORECASE):
            in_section = True
            continue

        if in_section and re.match(r"^##\s+", stripped):
            break

        if in_section:
            match = re.match(
                r"-\s+\*\*(.+?):\*\*\s*(.+)", stripped
            )
            if match:
                key = match.group(1).strip().lower().replace(" ", "_")
                value = match.group(2).strip()
                tech_stack[key] = value

    return tech_stack


def write_claude_md(project_dir: Path, content: str) -> None:
    """Write CLAUDE.md to the given directory."""
    claude_md_path = project_dir / "CLAUDE.md"
    claude_md_path.write_text(content, encoding="utf-8")
