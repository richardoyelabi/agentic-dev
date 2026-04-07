"""Tests for the structure_detector module."""

import pytest

from agentic_dev.config import DirectoryMap
from agentic_dev.onboarding.structure_detector import (
    StructureDetectionError,
    _parse_detection_result,
)


class TestParseDetectionResult:
    """Tests for parsing structure detector agent output."""

    def test_parses_valid_json(self):
        text = '{"frontend": "client", "backend": "server", "project_type": "fullstack"}'
        result = _parse_detection_result(text)
        assert result.frontend == "client"
        assert result.backend == "server"

    def test_parses_json_with_surrounding_text(self):
        text = 'Here is the result:\n{"frontend": "web", "backend": "api", "project_type": "fullstack"}\n'
        result = _parse_detection_result(text)
        assert result.frontend == "web"
        assert result.backend == "api"

    def test_parses_null_frontend(self):
        text = '{"frontend": null, "backend": ".", "project_type": "backend_only"}'
        result = _parse_detection_result(text)
        assert result.frontend is None
        assert result.backend == "."

    def test_parses_null_backend(self):
        text = '{"frontend": ".", "backend": null, "project_type": "frontend_only"}'
        result = _parse_detection_result(text)
        assert result.frontend == "."
        assert result.backend is None

    def test_raises_on_no_json(self):
        with pytest.raises(StructureDetectionError, match="valid JSON"):
            _parse_detection_result("No JSON here at all")

    def test_raises_on_invalid_json(self):
        with pytest.raises(StructureDetectionError, match="Failed to parse"):
            _parse_detection_result("{invalid json}")

    def test_returns_directory_map_type(self):
        text = '{"frontend": "src/web", "backend": "src/api", "project_type": "fullstack"}'
        result = _parse_detection_result(text)
        assert isinstance(result, DirectoryMap)

    def test_handles_monorepo_paths(self):
        text = '{"frontend": "apps/web", "backend": "apps/api", "project_type": "fullstack"}'
        result = _parse_detection_result(text)
        assert result.frontend == "apps/web"
        assert result.backend == "apps/api"
