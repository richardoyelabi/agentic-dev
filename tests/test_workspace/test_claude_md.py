"""Tests for the unified per-track CLAUDE.md generator and tech stack parser."""

from pathlib import Path

from agentic_dev.tracks import Track
from agentic_dev.workspace.claude_md import (
    generate_track_claude_md,
    parse_tech_stack,
    write_claude_md,
)


class TestGenerateTrackClaudeMd:
    def test_includes_project_name(self) -> None:
        track = Track(name="web", kind="web", uat_kind="web")
        content = generate_track_claude_md("MyApp", track, {})
        assert "MyApp" in content

    def test_header_mentions_track_name_and_kind(self) -> None:
        track = Track(name="api", kind="api", uat_kind="api")
        content = generate_track_claude_md("MyApp", track, {})
        assert "Track: api" in content
        assert "(api)" in content

    def test_web_kind_uses_react_defaults_and_api_layer_section(self) -> None:
        track = Track(name="web", kind="web", uat_kind="web")
        content = generate_track_claude_md("MyApp", track, {})
        assert "React" in content
        assert "## API Layer" in content
        assert "api_contract" in content

    def test_api_kind_uses_django_defaults_and_error_handling(self) -> None:
        track = Track(name="api", kind="api", uat_kind="api")
        content = generate_track_claude_md("MyApp", track, {})
        assert "Django REST Framework" in content
        assert "## Error Handling" in content

    def test_custom_framework_overrides_default(self) -> None:
        track = Track(name="web", kind="web", uat_kind="web")
        content = generate_track_claude_md("MyApp", track, {"framework": "Next.js"})
        assert "Next.js" in content

    def test_cli_kind_includes_cli_conventions(self) -> None:
        track = Track(name="cli", kind="cli", uat_kind="cli")
        content = generate_track_claude_md("MyApp", track, {})
        assert "CLI Conventions" in content
        assert "stderr" in content

    def test_worker_kind_includes_worker_conventions(self) -> None:
        track = Track(name="worker", kind="worker")
        content = generate_track_claude_md("MyApp", track, {})
        assert "Worker Conventions" in content
        assert "idempotent" in content


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
"""
        result = parse_tech_stack(spec)
        assert result["framework"] == "FastAPI"
        assert result["database"] == "PostgreSQL"
        assert result["testing"] == "Pytest"

    def test_returns_empty_dict_on_unparseable_input(self) -> None:
        result = parse_tech_stack("No tech stack section here.")
        assert result == {}

    def test_handles_empty_string(self) -> None:
        result = parse_tech_stack("")
        assert result == {}

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
