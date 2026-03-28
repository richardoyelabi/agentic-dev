# Agentic-Dev

Autonomous software development agency powered by Claude Code CLI.

## Tech Stack
- Python 3.12+
- Typer (CLI), Pydantic (models), Jinja2 (templates), Rich (terminal UI), PyYAML (config)
- Pytest for testing (with pytest-asyncio for async tests)

## Conventions
- Use double quotes for strings
- Use Pydantic for all data models
- Use async/await for subprocess calls to Claude CLI
- Agent definitions are YAML files in `src/agentic_dev/agents/definitions/`
- Prompt templates are Jinja2 files in `src/agentic_dev/prompts/templates/`
- Tests mirror the src structure under `tests/`

## Running Tests
```bash
pytest
```

## Design Spec
See `docs/superpowers/specs/2026-03-28-agentic-dev-agency-design.md`
