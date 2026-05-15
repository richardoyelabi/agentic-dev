"""Tests for the environment detector — bootstrap.md + secrets.env synthesis."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.exceptions import AgentRunError
from agentic_dev.onboarding.environment import (
    ENVIRONMENT_DETECTOR_PROMPT,
    EnvironmentReport,
    detect_environment,
    parse_environment_response,
    parse_install_commands,
)
from agentic_dev.tracks import Track


_VALID_RESPONSE = """\
Some preamble from the LLM.

<<<BOOTSTRAP_MD>>>
# Bootstrap

## backend
- Install: `pip install -r requirements.txt`
- Run: `uvicorn app:app --port 8000`
<<<END_BOOTSTRAP_MD>>>

<<<ENV_REQUIREMENTS_MD>>>
# Env requirements

## backend
- DJANGO_SECRET_KEY (auto)
- AGORA_APP_ID (human)
<<<END_ENV_REQUIREMENTS_MD>>>

<<<SECRETS_ENV>>>
DJANGO_SECRET_KEY=abc123
AGORA_APP_ID=<FILL ME: Agora console>
<<<END_SECRETS_ENV>>>
"""


def _make_claude_result(text: str = _VALID_RESPONSE) -> ClaudeResult:
    return ClaudeResult(
        text=text,
        session_id="test-session",
        cost_usd=0.05,
        exit_code=0,
        raw_json={},
    )


def _make_mock_runner(return_value: ClaudeResult | None = None) -> MagicMock:
    mock = MagicMock(spec=ClaudeRunner)
    mock.run = AsyncMock(return_value=return_value or _make_claude_result())
    return mock


class TestParseEnvironmentResponse:
    """``parse_environment_response`` extracts three fenced sections."""

    def test_extracts_all_three_sections(self) -> None:
        report = parse_environment_response(_VALID_RESPONSE)

        assert "## backend" in report.bootstrap_md
        assert "pip install" in report.bootstrap_md
        assert "DJANGO_SECRET_KEY" in report.env_requirements_md
        assert "DJANGO_SECRET_KEY=abc123" in report.secrets_env_template
        assert "<FILL ME: Agora console>" in report.secrets_env_template

    def test_missing_section_raises(self) -> None:
        text = "no fenced sections here at all"
        with pytest.raises(ValueError, match="BOOTSTRAP_MD"):
            parse_environment_response(text)

    def test_strips_section_whitespace(self) -> None:
        text = (
            "<<<BOOTSTRAP_MD>>>\n   # B   \n<<<END_BOOTSTRAP_MD>>>\n"
            "<<<ENV_REQUIREMENTS_MD>>>\n# E\n<<<END_ENV_REQUIREMENTS_MD>>>\n"
            "<<<SECRETS_ENV>>>\nK=V\n<<<END_SECRETS_ENV>>>\n"
        )
        report = parse_environment_response(text)
        assert report.bootstrap_md == "# B"
        assert report.env_requirements_md == "# E"
        assert report.secrets_env_template == "K=V"


class TestParseInstallCommands:
    """``parse_install_commands`` extracts a per-track install command map."""

    def test_extracts_one_command_per_track(self) -> None:
        md = (
            "# Bootstrap\n\n"
            "## backend\n"
            "- Install: `pip install -r requirements.txt`\n"
            "- Run: `uvicorn app:app`\n\n"
            "## frontend\n"
            "- Install: `yarn install`\n"
            "- Run: `yarn dev`\n"
        )
        assert parse_install_commands(md) == {
            "backend": "pip install -r requirements.txt",
            "frontend": "yarn install",
        }

    def test_omits_tracks_without_install_line(self) -> None:
        md = (
            "## backend\n- Install: `make install`\n\n"
            "## docs\n- Run: `mkdocs serve`\n"
        )
        assert parse_install_commands(md) == {"backend": "make install"}

    def test_handles_root_section(self) -> None:
        md = (
            "## Root\n- Install: `make bootstrap`\n\n"
            "## backend\n- Run: `uvicorn x`\n"
        )
        assert parse_install_commands(md) == {"Root": "make bootstrap"}

    def test_empty_when_no_sections(self) -> None:
        assert parse_install_commands("# Bootstrap\nno sections\n") == {}

    def test_ignores_install_outside_track_section(self) -> None:
        md = "Random text mentioning install but no headers\n"
        assert parse_install_commands(md) == {}


class TestDetectEnvironment:
    async def test_constructs_correct_agent_config(self, tmp_path: Path) -> None:
        mock_runner = _make_mock_runner()

        await detect_environment(mock_runner, tmp_path, tracks=[])

        config = mock_runner.run.call_args.kwargs["agent"]
        assert config.name == "environment_detector"
        assert config.model == "sonnet"
        assert config.permission_mode == "plan"
        assert config.allowed_tools == ["Read", "Glob", "Grep"]
        assert config.use_bare_mode is True

    async def test_runs_at_project_root(self, tmp_path: Path) -> None:
        mock_runner = _make_mock_runner()

        await detect_environment(mock_runner, tmp_path, tracks=[])

        assert mock_runner.run.call_args.kwargs["working_dir"] == tmp_path

    async def test_prompt_lists_tracks_with_paths_and_kinds(
        self, tmp_path: Path
    ) -> None:
        mock_runner = _make_mock_runner()
        tracks = [
            Track(name="backend", path="backend", kind="api", uat_kind="api"),
            Track(name="frontend", path="frontend", kind="web", uat_kind="web"),
        ]

        await detect_environment(mock_runner, tmp_path, tracks=tracks)

        prompt = mock_runner.run.call_args.kwargs["prompt"]
        assert "backend" in prompt
        assert "frontend" in prompt
        assert "api" in prompt
        assert "web" in prompt
        assert ENVIRONMENT_DETECTOR_PROMPT in prompt

    async def test_returns_parsed_report(self, tmp_path: Path) -> None:
        mock_runner = _make_mock_runner()

        report = await detect_environment(mock_runner, tmp_path, tracks=[])

        assert isinstance(report, EnvironmentReport)
        assert "pip install" in report.bootstrap_md
        assert "AGORA_APP_ID" in report.env_requirements_md
        assert "AGORA_APP_ID=<FILL ME" in report.secrets_env_template

    async def test_propagates_agent_run_error(self, tmp_path: Path) -> None:
        mock_runner = _make_mock_runner()
        mock_runner.run.side_effect = AgentRunError(
            agent_name="environment_detector",
            message="timeout",
        )

        with pytest.raises(AgentRunError, match="environment_detector"):
            await detect_environment(mock_runner, tmp_path, tracks=[])
