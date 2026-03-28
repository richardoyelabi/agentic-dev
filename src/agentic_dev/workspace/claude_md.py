"""Generator for CLAUDE.md files tailored to frontend and backend repos."""

from pathlib import Path


def generate_frontend_claude_md(
    project_name: str, tech_stack: dict[str, str]
) -> str:
    """Generate CLAUDE.md content for a frontend repository.

    The tech_stack dict should contain keys like "framework", "styling",
    "state_management", and "testing".
    """
    framework = tech_stack.get("framework", "React")
    styling = tech_stack.get("styling", "Tailwind CSS")
    state_management = tech_stack.get("state_management", "TanStack Query")
    testing = tech_stack.get("testing", "Vitest + React Testing Library")

    return f"""\
# {project_name} — Frontend

## Tech Stack
- **Framework:** {framework}
- **Styling:** {styling}
- **State Management:** {state_management}
- **Testing:** {testing}

## Coding Conventions
- Use double quotes for strings
- Use functional components only
- Keep components small and focused on a single responsibility
- Co-locate tests next to the files they test

## Testing
- Framework: {testing}
- Always practise test-driven development
- Write tests before implementing components and features

## API Layer
- All API interactions must conform to the API Contract in `../docs/api_contract.md`
- Use a shared API client module for all backend requests
- Never hardcode endpoint URLs — reference the API Contract for paths and schemas

## Superpowers
- Always practise test-driven development
- Use the brainstorming skill before adding new components
- Use systematic debugging when tests fail
- Run verification before claiming work is complete
"""


def generate_backend_claude_md(
    project_name: str, tech_stack: dict[str, str]
) -> str:
    """Generate CLAUDE.md content for a backend repository.

    The tech_stack dict should contain keys like "framework", "database",
    and "testing".
    """
    framework = tech_stack.get("framework", "Django REST Framework")
    database = tech_stack.get("database", "PostgreSQL")
    testing = tech_stack.get("testing", "Pytest")

    return f"""\
# {project_name} — Backend

## Tech Stack
- **Framework:** {framework}
- **Database:** {database}
- **Testing:** {testing}

## Coding Conventions
- Use double quotes for strings
- Follow the framework's idiomatic patterns
- Keep business logic in service modules, not in views or serializers
- Use type hints on all function signatures

## Testing
- Framework: {testing}
- Always practise test-driven development
- Write tests before implementing services and endpoints

## API Layer
- All endpoints must conform to the API Contract in `../docs/api_contract.md`
- The API Contract is the single source of truth for request/response schemas
- Never deviate from the contracted endpoint paths or payload shapes

## Superpowers
- Always practise test-driven development
- Use the brainstorming skill before adding new components
- Use systematic debugging when tests fail
- Run verification before claiming work is complete
"""


def write_claude_md(project_dir: Path, content: str) -> None:
    """Write CLAUDE.md to the given directory."""
    claude_md_path = project_dir / "CLAUDE.md"
    claude_md_path.write_text(content, encoding="utf-8")
