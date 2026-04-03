"""Tests for CLAUDE.md generation and tech stack parsing."""

from pathlib import Path

import pytest

from agentic_dev.workspace.claude_md import (
    generate_backend_claude_md,
    generate_frontend_claude_md,
    parse_tech_stack,
    write_claude_md,
)


class TestGenerateFrontendClaudeMd:
    def test_includes_project_name(self) -> None:
        content = generate_frontend_claude_md("MyApp", {})
        assert "MyApp" in content

    def test_includes_custom_framework(self) -> None:
        content = generate_frontend_claude_md("MyApp", {"framework": "Next.js"})
        assert "Next.js" in content

    def test_includes_default_framework_when_not_specified(self) -> None:
        content = generate_frontend_claude_md("MyApp", {})
        assert "React" in content

    def test_includes_styling(self) -> None:
        content = generate_frontend_claude_md("MyApp", {"styling": "CSS Modules"})
        assert "CSS Modules" in content

    def test_includes_api_contract_reference(self) -> None:
        content = generate_frontend_claude_md("MyApp", {})
        assert "api_contract" in content

    def test_includes_error_handling_section(self) -> None:
        content = generate_frontend_claude_md("MyApp", {})
        assert "## Error Handling" in content
        assert "error boundaries" in content
        assert "user-friendly messages" in content


class TestGenerateBackendClaudeMd:
    def test_includes_project_name(self) -> None:
        content = generate_backend_claude_md("MyApp", {})
        assert "MyApp" in content

    def test_includes_custom_framework(self) -> None:
        content = generate_backend_claude_md("MyApp", {"framework": "FastAPI"})
        assert "FastAPI" in content

    def test_includes_default_framework_when_not_specified(self) -> None:
        content = generate_backend_claude_md("MyApp", {})
        assert "Django REST Framework" in content

    def test_includes_database(self) -> None:
        content = generate_backend_claude_md("MyApp", {"database": "MongoDB"})
        assert "MongoDB" in content

    def test_includes_api_contract_reference(self) -> None:
        content = generate_backend_claude_md("MyApp", {})
        assert "api_contract" in content

    def test_includes_error_handling_section(self) -> None:
        content = generate_backend_claude_md("MyApp", {})
        assert "## Error Handling" in content
        assert "consistent error response schema" in content
        assert "internal error details" in content


class TestWriteClaudeMd:
    def test_creates_file(self, tmp_path: Path) -> None:
        write_claude_md(tmp_path, "# Test Content")
        assert (tmp_path / "CLAUDE.md").exists()

    def test_writes_correct_content(self, tmp_path: Path) -> None:
        write_claude_md(tmp_path, "# Test Content")
        assert (tmp_path / "CLAUDE.md").read_text() == "# Test Content"

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        write_claude_md(tmp_path, "old content")
        write_claude_md(tmp_path, "new content")
        assert (tmp_path / "CLAUDE.md").read_text() == "new content"


class TestParseTechStack:
    def test_extracts_framework(self) -> None:
        spec = """\
## Tech Stack
- **Framework:** Next.js
- **Database:** PostgreSQL
"""
        result = parse_tech_stack(spec)
        assert result["framework"] == "Next.js"

    def test_extracts_multiple_keys(self) -> None:
        spec = """\
## Tech Stack
- **Framework:** FastAPI
- **Database:** PostgreSQL
- **Testing:** Pytest
- **Styling:** Tailwind CSS
"""
        result = parse_tech_stack(spec)
        assert result["framework"] == "FastAPI"
        assert result["database"] == "PostgreSQL"
        assert result["testing"] == "Pytest"
        assert result["styling"] == "Tailwind CSS"

    def test_extracts_state_management(self) -> None:
        spec = """\
## Tech Stack
- **State Management:** Redux Toolkit
"""
        result = parse_tech_stack(spec)
        assert result["state_management"] == "Redux Toolkit"

    def test_returns_empty_dict_on_unparseable_input(self) -> None:
        result = parse_tech_stack("No tech stack section here.")
        assert result == {}

    def test_handles_empty_string(self) -> None:
        result = parse_tech_stack("")
        assert result == {}

    def test_handles_tech_stack_with_extra_whitespace(self) -> None:
        spec = """\
## Tech Stack
-  **Framework:**   Django REST Framework
"""
        result = parse_tech_stack(spec)
        assert result["framework"] == "Django REST Framework"

    def test_stops_at_next_section(self) -> None:
        spec = """\
## Tech Stack
- **Framework:** Next.js

## Architecture
- **Pattern:** Microservices
"""
        result = parse_tech_stack(spec)
        assert result == {"framework": "Next.js"}
        assert "pattern" not in result
