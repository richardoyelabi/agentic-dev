"""Tests for the ClaudeRunner subprocess wrapper."""

import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.exceptions import AgentRunError


@dataclass
class FakeAgentConfig:
    """Minimal agent config satisfying the AgentConfig protocol."""

    name: str = "test-agent"
    model: str = "sonnet"
    permission_mode: str = "plan"
    allowed_tools: list[str] | None = None
    max_turns: int = 50
    use_bare_mode: bool = False
    mcp_config: Path | None = None
    system_prompt: str | None = None

    def __post_init__(self):
        if self.allowed_tools is None:
            self.allowed_tools = ["Read", "Glob"]


class TestBuildCommand:
    """Tests for ClaudeRunner.build_command with various configurations."""

    def test_basic_command(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig()
        cmd = runner.build_command(agent, "Do something", tmp_path)

        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "--output-format" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "json"
        assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-6"
        assert cmd[cmd.index("--permission-mode") + 1] == "plan"
        assert cmd[cmd.index("--max-turns") + 1] == "50"
        assert cmd[-1] == "Do something"

    def test_opus_model_resolution(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig(model="opus")
        cmd = runner.build_command(agent, "prompt", tmp_path)

        assert cmd[cmd.index("--model") + 1] == "claude-opus-4-6"

    def test_full_model_id_passthrough(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig(model="claude-opus-4-6")
        cmd = runner.build_command(agent, "prompt", tmp_path)

        assert cmd[cmd.index("--model") + 1] == "claude-opus-4-6"

    def test_allowed_tools_joined(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig(allowed_tools=["Read", "Glob", "Grep"])
        cmd = runner.build_command(agent, "prompt", tmp_path)

        assert cmd[cmd.index("--allowedTools") + 1] == "Read,Glob,Grep"

    def test_empty_allowed_tools_omitted(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig(allowed_tools=[])
        cmd = runner.build_command(agent, "prompt", tmp_path)

        assert "--allowedTools" not in cmd

    def test_session_resume_flag(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig()
        cmd = runner.build_command(agent, "prompt", tmp_path, session_id="sess-abc-123")

        assert cmd[cmd.index("--resume") + 1] == "sess-abc-123"

    def test_no_session_id_omits_resume(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig()
        cmd = runner.build_command(agent, "prompt", tmp_path)

        assert "--resume" not in cmd

    def test_mcp_config_flag(self, tmp_path: Path):
        runner = ClaudeRunner()
        mcp_path = tmp_path / "stripe.json"
        agent = FakeAgentConfig(mcp_config=mcp_path)
        cmd = runner.build_command(agent, "prompt", tmp_path)

        assert cmd[cmd.index("--mcp-config") + 1] == str(mcp_path)

    def test_extra_add_dirs(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig()
        dir1 = tmp_path / "frontend"
        dir2 = tmp_path / "backend"
        cmd = runner.build_command(agent, "prompt", tmp_path, extra_add_dirs=[dir1, dir2])

        add_dir_indices = [i for i, v in enumerate(cmd) if v == "--add-dir"]
        assert len(add_dir_indices) == 2
        assert cmd[add_dir_indices[0] + 1] == str(dir1)
        assert cmd[add_dir_indices[1] + 1] == str(dir2)

    def test_system_prompt_flag(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig(system_prompt="You are a helpful architect.")
        cmd = runner.build_command(agent, "prompt", tmp_path)

        assert cmd[cmd.index("--system-prompt") + 1] == "You are a helpful architect."

    def test_bypass_permissions_mode(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig(permission_mode="bypassPermissions")
        cmd = runner.build_command(agent, "prompt", tmp_path)

        assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"


class TestRun:
    """Tests for ClaudeRunner.run with mocked subprocess."""

    @staticmethod
    def _make_mock_process(stdout: str, returncode: int = 0, stderr: str = ""):
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (
            stdout.encode("utf-8"),
            stderr.encode("utf-8"),
        )
        mock_process.returncode = returncode
        return mock_process

    async def test_successful_run(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig()
        output_json = json.dumps({
            "result": "Here is the spec.",
            "session_id": "sess-001",
            "cost_usd": 0.42,
        })
        mock_process = self._make_mock_process(output_json)

        with patch("agentic_dev.claude.runner.asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            result = await runner.run(agent, "Build a spec", tmp_path)

        assert isinstance(result, ClaudeResult)
        assert result.text == "Here is the spec."
        assert result.session_id == "sess-001"
        assert result.cost_usd == pytest.approx(0.42)
        assert result.exit_code == 0

        mock_exec.assert_called_once()
        call_args = mock_exec.call_args
        assert call_args.kwargs["cwd"] == str(tmp_path)

    async def test_non_zero_exit_raises_agent_run_error(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig(name="architect")
        mock_process = self._make_mock_process(
            stdout="", returncode=1, stderr="Something went wrong"
        )

        with patch("agentic_dev.claude.runner.asyncio.create_subprocess_exec", return_value=mock_process):
            with pytest.raises(AgentRunError, match="architect") as exc_info:
                await runner.run(agent, "prompt", tmp_path)

        assert exc_info.value.exit_code == 1
        assert "Something went wrong" in str(exc_info.value)

    async def test_invalid_json_output_raises_agent_run_error(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig(name="planner")
        mock_process = self._make_mock_process(stdout="not json at all")

        with patch("agentic_dev.claude.runner.asyncio.create_subprocess_exec", return_value=mock_process):
            with pytest.raises(AgentRunError, match="planner"):
                await runner.run(agent, "prompt", tmp_path)

    async def test_missing_fields_use_defaults(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig()
        output_json = json.dumps({"result": "ok"})
        mock_process = self._make_mock_process(output_json)

        with patch("agentic_dev.claude.runner.asyncio.create_subprocess_exec", return_value=mock_process):
            result = await runner.run(agent, "prompt", tmp_path)

        assert result.session_id is None
        assert result.cost_usd == 0.0
