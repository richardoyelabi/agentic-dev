"""Tests for the structure_detector module."""

import pytest

from agentic_dev.config import DirectoryMap
from agentic_dev.onboarding.structure_detector import (
    DetectionResult,
    StructureDetectionError,
    _parse_detection_result,
)
from agentic_dev.state.models import FrontendKind


class TestParseDetectionResult:
    """Tests for parsing structure detector agent output."""

    def test_parses_valid_json(self):
        text = '{"frontend": "client", "backend": "server", "project_type": "fullstack", "frontend_kind": "web"}'
        result = _parse_detection_result(text)
        assert result.directory_map.frontend == "client"
        assert result.directory_map.backend == "server"
        assert result.frontend_kind == FrontendKind.WEB

    def test_parses_json_with_surrounding_text(self):
        text = 'Here is the result:\n{"frontend": "web", "backend": "api", "project_type": "fullstack", "frontend_kind": "web"}\n'
        result = _parse_detection_result(text)
        assert result.directory_map.frontend == "web"
        assert result.directory_map.backend == "api"

    def test_parses_null_frontend_defaults_kind_to_none(self):
        text = '{"frontend": null, "backend": ".", "project_type": "backend_only", "frontend_kind": "none"}'
        result = _parse_detection_result(text)
        assert result.directory_map.frontend is None
        assert result.directory_map.backend == "."
        assert result.frontend_kind == FrontendKind.NONE

    def test_parses_null_backend(self):
        text = '{"frontend": ".", "backend": null, "project_type": "frontend_only", "frontend_kind": "web"}'
        result = _parse_detection_result(text)
        assert result.directory_map.frontend == "."
        assert result.directory_map.backend is None

    def test_parses_cli_kind(self):
        text = '{"frontend": ".", "backend": null, "project_type": "frontend_only", "frontend_kind": "cli"}'
        result = _parse_detection_result(text)
        assert result.frontend_kind == FrontendKind.CLI

    def test_parses_mobile_kind(self):
        text = '{"frontend": ".", "backend": null, "project_type": "frontend_only", "frontend_kind": "mobile"}'
        result = _parse_detection_result(text)
        assert result.frontend_kind == FrontendKind.MOBILE

    def test_missing_kind_defaults_to_web_when_frontend_exists(self):
        """Conservative default: if frontend dir is present but kind is absent."""
        text = '{"frontend": ".", "backend": null, "project_type": "frontend_only"}'
        result = _parse_detection_result(text)
        assert result.frontend_kind == FrontendKind.WEB

    def test_missing_kind_defaults_to_none_when_no_frontend(self):
        text = '{"frontend": null, "backend": ".", "project_type": "backend_only"}'
        result = _parse_detection_result(text)
        assert result.frontend_kind == FrontendKind.NONE

    def test_unknown_kind_falls_back_to_web(self):
        text = '{"frontend": ".", "backend": null, "project_type": "frontend_only", "frontend_kind": "smart_fridge"}'
        result = _parse_detection_result(text)
        assert result.frontend_kind == FrontendKind.WEB

    def test_raises_on_no_json(self):
        with pytest.raises(StructureDetectionError, match="valid JSON"):
            _parse_detection_result("No JSON here at all")

    def test_raises_on_invalid_json(self):
        with pytest.raises(StructureDetectionError, match="Failed to parse"):
            _parse_detection_result("{invalid json}")

    def test_returns_detection_result_type(self):
        text = '{"frontend": "src/web", "backend": "src/api", "project_type": "fullstack", "frontend_kind": "web"}'
        result = _parse_detection_result(text)
        assert isinstance(result, DetectionResult)
        assert isinstance(result.directory_map, DirectoryMap)

    def test_handles_monorepo_paths(self):
        text = '{"frontend": "apps/web", "backend": "apps/api", "project_type": "fullstack", "frontend_kind": "web"}'
        result = _parse_detection_result(text)
        assert result.directory_map.frontend == "apps/web"
        assert result.directory_map.backend == "apps/api"
