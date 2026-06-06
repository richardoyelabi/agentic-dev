"""Tests for the ClaudeRunner subprocess wrapper."""

import asyncio
import json
import os
import signal
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agentic_dev.claude.runner import (
    DEFAULT_AGENT_BACKSTOP_S,
    DEFAULT_AGENT_IDLE_TIMEOUT_S,
    ClaudeResult,
    ClaudeRunner,
    _collect_output,
    _latest_session_activity,
    _snapshot_process_group,
    _terminate_process_group,
)
from agentic_dev.exceptions import AgentRunError, RateLimitError


@pytest.fixture(autouse=True)
def _never_signal_real_process_groups():
    """Safety net: no test in this module may deliver a real OS signal.

    ``_terminate_process_group`` calls ``os.killpg``; a mock process whose
    ``pid`` happens to map to a live group would otherwise signal it for real
    (this has crashed the dev machine). Neutralise ``killpg`` globally; tests
    that assert on it re-patch within their own ``with`` block.
    """
    with patch("agentic_dev.claude.runner.os.killpg"):
        yield


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
    timeout_s: int | None = None
    idle_timeout_s: int | None = None

    def __post_init__(self):
        if self.allowed_tools is None:
            self.allowed_tools = ["Read", "Glob"]


class TestBuildCommand:
    """Tests for ClaudeRunner.build_command with various configurations."""

    def test_basic_command(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig()
        cmd = runner.build_command(agent, tmp_path)

        assert cmd[0] == "claude"
        assert cmd[1] == "-p"
        assert cmd[2] == "-"
        assert "--output-format" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "json"
        assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-6"
        assert cmd[cmd.index("--permission-mode") + 1] == "plan"
        assert cmd[cmd.index("--max-turns") + 1] == "50"

    def test_opus_model_resolution(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig(model="opus")
        cmd = runner.build_command(agent, tmp_path)

        assert cmd[cmd.index("--model") + 1] == "claude-opus-4-6"

    def test_full_model_id_passthrough(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig(model="claude-opus-4-6")
        cmd = runner.build_command(agent, tmp_path)

        assert cmd[cmd.index("--model") + 1] == "claude-opus-4-6"

    def test_allowed_tools_joined(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig(allowed_tools=["Read", "Glob", "Grep"])
        cmd = runner.build_command(agent, tmp_path)

        assert cmd[cmd.index("--allowedTools") + 1] == "Read,Glob,Grep"

    def test_empty_allowed_tools_passes_empty_string(self, tmp_path: Path):
        """Explicitly disable all tools by passing --allowedTools "" rather than omitting."""
        runner = ClaudeRunner()
        agent = FakeAgentConfig(allowed_tools=[])
        cmd = runner.build_command(agent, tmp_path)

        assert "--allowedTools" in cmd
        assert cmd[cmd.index("--allowedTools") + 1] == ""

    def test_session_resume_flag(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig()
        cmd = runner.build_command(agent, tmp_path, session_id="sess-abc-123")

        assert cmd[cmd.index("--resume") + 1] == "sess-abc-123"

    def test_no_session_id_omits_resume(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig()
        cmd = runner.build_command(agent, tmp_path)

        assert "--resume" not in cmd

    def test_mcp_config_flag(self, tmp_path: Path):
        runner = ClaudeRunner()
        mcp_path = tmp_path / "stripe.json"
        agent = FakeAgentConfig(mcp_config=mcp_path)
        cmd = runner.build_command(agent, tmp_path)

        assert cmd[cmd.index("--mcp-config") + 1] == str(mcp_path)

    def test_extra_add_dirs(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig()
        dir1 = tmp_path / "frontend"
        dir2 = tmp_path / "backend"
        cmd = runner.build_command(agent, tmp_path, extra_add_dirs=[dir1, dir2])

        add_dir_indices = [i for i, v in enumerate(cmd) if v == "--add-dir"]
        assert len(add_dir_indices) == 2
        assert cmd[add_dir_indices[0] + 1] == str(dir1)
        assert cmd[add_dir_indices[1] + 1] == str(dir2)

    def test_system_prompt_flag(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig(system_prompt="You are a helpful architect.")
        cmd = runner.build_command(agent, tmp_path)

        assert cmd[cmd.index("--system-prompt") + 1] == "You are a helpful architect."

    def test_bypass_permissions_mode(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig(permission_mode="bypassPermissions")
        cmd = runner.build_command(agent, tmp_path)

        assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"

    def test_bare_mode_never_added(self, tmp_path: Path):
        """--bare breaks OAuth auth so we never add it regardless of use_bare_mode."""
        runner = ClaudeRunner()
        for bare in (True, False):
            agent = FakeAgentConfig(use_bare_mode=bare)
            cmd = runner.build_command(agent, tmp_path)
            assert "--bare" not in cmd


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
            "total_cost_usd": 0.42,
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
        assert call_args.kwargs["stdin"] == asyncio.subprocess.PIPE

    async def test_prompt_piped_via_stdin(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig()
        output_json = json.dumps({"result": "ok", "total_cost_usd": 0.01})
        mock_process = self._make_mock_process(output_json)

        with patch("agentic_dev.claude.runner.asyncio.create_subprocess_exec", return_value=mock_process):
            await runner.run(agent, "My long prompt", tmp_path)

        mock_process.communicate.assert_called_once_with(
            input=b"My long prompt"
        )

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


class TestActivityTailer:
    """The live transcript-activity tailer is scoped to each subprocess."""

    @staticmethod
    def _make_mock_process(stdout: str, returncode: int = 0, stderr: str = ""):
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (
            stdout.encode("utf-8"),
            stderr.encode("utf-8"),
        )
        mock_process.returncode = returncode
        return mock_process

    @staticmethod
    def _recording_tail(state: dict):
        def factory(*args, **kwargs):
            state["args"] = args
            state["kwargs"] = kwargs

            async def _coro():
                state["started"] = True
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    state["cancelled"] = True
                    raise

            return _coro()

        return factory

    async def test_tailer_started_with_run_args_and_cancelled_on_success(
        self, tmp_path: Path
    ):
        runner = ClaudeRunner()
        agent = FakeAgentConfig(name="architect")
        state: dict = {"started": False, "cancelled": False}
        mock_process = self._make_mock_process(
            json.dumps({"result": "ok", "total_cost_usd": 0.0})
        )

        async def fake_collect(process, prompt, working_dir, backstop_s, idle_timeout_s):
            await asyncio.sleep(0.02)  # yield so the tailer task can start
            return (b'{"result": "ok", "total_cost_usd": 0.0}', b"", None)

        with patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ), patch(
            "agentic_dev.claude.runner.tail_transcript_activity",
            self._recording_tail(state),
        ), patch(
            "agentic_dev.claude.runner._collect_output", fake_collect
        ):
            result = await runner.run(agent, "prompt", tmp_path)

        assert result.text == "ok"
        assert state["args"][0] == tmp_path  # working_dir
        assert state["args"][3] == "architect"  # agent name
        assert state["started"] is True
        assert state["cancelled"] is True

    async def test_tailer_cancelled_when_run_raises(self, tmp_path: Path):
        runner = ClaudeRunner()
        agent = FakeAgentConfig(name="planner")
        state: dict = {"started": False, "cancelled": False}
        mock_process = self._make_mock_process(stdout="", returncode=1, stderr="boom")

        async def fake_collect(process, prompt, working_dir, backstop_s, idle_timeout_s):
            await asyncio.sleep(0.02)
            return (b"", b"boom", None)

        with patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ), patch(
            "agentic_dev.claude.runner.tail_transcript_activity",
            self._recording_tail(state),
        ), patch(
            "agentic_dev.claude.runner._collect_output", fake_collect
        ):
            with pytest.raises(AgentRunError):
                await runner.run(agent, "prompt", tmp_path)

        assert state["started"] is True
        assert state["cancelled"] is True


class TestLogging:
    """Tests for agent execution logging."""

    async def test_saves_log_file(self, tmp_path: Path):
        log_dir = tmp_path / "logs"
        runner = ClaudeRunner(log_dir=log_dir)
        agent = FakeAgentConfig(name="test_agent")
        output_json = json.dumps({"result": "output", "total_cost_usd": 0.1})
        mock_process = TestRun._make_mock_process(output_json)

        with patch("agentic_dev.claude.runner.asyncio.create_subprocess_exec", return_value=mock_process):
            await runner.run(agent, "test prompt", tmp_path)

        log_files = list((log_dir / "agent_dumps").glob("test_agent_*.json"))
        assert len(log_files) == 1

        log_data = json.loads(log_files[0].read_text(encoding="utf-8"))
        assert log_data["agent_name"] == "test_agent"
        assert log_data["prompt"] == "test prompt"
        assert log_data["result_length"] == len("output")
        assert log_data["cost_usd"] == pytest.approx(0.1)

    async def test_no_log_dir_skips_logging(self, tmp_path: Path):
        runner = ClaudeRunner(log_dir=None)
        agent = FakeAgentConfig()
        output_json = json.dumps({"result": "ok"})
        mock_process = TestRun._make_mock_process(output_json)

        with patch("agentic_dev.claude.runner.asyncio.create_subprocess_exec", return_value=mock_process):
            result = await runner.run(agent, "prompt", tmp_path)

        assert result.text == "ok"


class TestRunFallbackToSession:
    """Tests for the run() method falling back to session JSONL on empty result."""

    @staticmethod
    def _make_mock_process(stdout: str, returncode: int = 0, stderr: str = ""):
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (
            stdout.encode("utf-8"),
            stderr.encode("utf-8"),
        )
        mock_process.returncode = returncode
        return mock_process

    @pytest.mark.asyncio
    async def test_falls_back_to_session_jsonl_when_result_empty(self, tmp_path: Path):
        """When CLI returns empty result but session JSONL has text, use the JSONL text."""
        runner = ClaudeRunner()
        agent = FakeAgentConfig()
        output_json = json.dumps({
            "result": "",
            "session_id": "sess-fallback",
            "total_cost_usd": 1.50,
        })
        mock_process = self._make_mock_process(output_json)

        with patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ), patch.object(
            ClaudeRunner, "_recover_result_from_session",
            return_value="Recovered summary text",
        ) as mock_recover:
            result = await runner.run(agent, "prompt", tmp_path)

        mock_recover.assert_called_once_with("sess-fallback", tmp_path)
        assert result.text == "Recovered summary text"
        assert result.cost_usd == pytest.approx(1.50)

    @pytest.mark.asyncio
    async def test_no_fallback_when_result_present(self, tmp_path: Path):
        """When CLI returns non-empty result, no fallback attempted."""
        runner = ClaudeRunner()
        agent = FakeAgentConfig()
        output_json = json.dumps({
            "result": "Normal result",
            "session_id": "sess-ok",
            "total_cost_usd": 0.50,
        })
        mock_process = self._make_mock_process(output_json)

        with patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ), patch.object(
            ClaudeRunner, "_recover_result_from_session",
        ) as mock_recover:
            result = await runner.run(agent, "prompt", tmp_path)

        mock_recover.assert_not_called()
        assert result.text == "Normal result"

    @pytest.mark.asyncio
    async def test_fallback_returns_empty_still_reports_empty(self, tmp_path: Path):
        """When both CLI and fallback return empty, result stays empty."""
        runner = ClaudeRunner()
        agent = FakeAgentConfig()
        output_json = json.dumps({
            "result": "",
            "session_id": "sess-empty",
            "total_cost_usd": 0.10,
        })
        mock_process = self._make_mock_process(output_json)

        with patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ), patch.object(
            ClaudeRunner, "_recover_result_from_session",
            return_value="",
        ):
            result = await runner.run(agent, "prompt", tmp_path)

        assert result.text == ""


class TestRecoverResultFromSession:
    """Tests for _recover_result_from_session JSONL fallback."""

    def test_extracts_last_assistant_text(self, tmp_path: Path):
        """Extracts text from the last assistant message in the JSONL."""
        session_id = "test-session-123"
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        # Create the JSONL path matching the CLI convention
        encoded = str(project_dir).replace("/", "-")
        sessions_dir = tmp_path / ".claude" / "projects" / encoded
        sessions_dir.mkdir(parents=True)
        jsonl_path = sessions_dir / f"{session_id}.jsonl"

        jsonl_path.write_text(
            json.dumps({"type": "human", "message": {"content": "do something"}}) + "\n"
            + json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Write", "input": {}},
            ]}}) + "\n"
            + json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Here is the summary of work done."},
            ]}}) + "\n"
            + json.dumps({"type": "last-prompt"}) + "\n",
            encoding="utf-8",
        )

        result = ClaudeRunner._recover_result_from_session(
            session_id, project_dir, claude_dir=tmp_path / ".claude",
        )
        assert result == "Here is the summary of work done."

    def test_returns_empty_when_no_text_blocks(self, tmp_path: Path):
        """Returns empty string when assistant messages have only tool_use blocks."""
        session_id = "test-no-text"
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        encoded = str(project_dir).replace("/", "-")
        sessions_dir = tmp_path / ".claude" / "projects" / encoded
        sessions_dir.mkdir(parents=True)
        jsonl_path = sessions_dir / f"{session_id}.jsonl"

        jsonl_path.write_text(
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {}},
            ]}}) + "\n",
            encoding="utf-8",
        )

        result = ClaudeRunner._recover_result_from_session(
            session_id, project_dir, claude_dir=tmp_path / ".claude",
        )
        assert result == ""

    def test_returns_empty_when_file_missing(self, tmp_path: Path):
        """Returns empty string when session JSONL does not exist."""
        result = ClaudeRunner._recover_result_from_session(
            "nonexistent", tmp_path, claude_dir=tmp_path / ".claude",
        )
        assert result == ""

    def test_concatenates_multiple_text_blocks(self, tmp_path: Path):
        """Concatenates all text blocks from the last assistant message."""
        session_id = "test-multi-text"
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        encoded = str(project_dir).replace("/", "-")
        sessions_dir = tmp_path / ".claude" / "projects" / encoded
        sessions_dir.mkdir(parents=True)
        jsonl_path = sessions_dir / f"{session_id}.jsonl"

        jsonl_path.write_text(
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Part one."},
                {"type": "tool_use", "name": "Read", "input": {}},
                {"type": "text", "text": " Part two."},
            ]}}) + "\n",
            encoding="utf-8",
        )

        result = ClaudeRunner._recover_result_from_session(
            session_id, project_dir, claude_dir=tmp_path / ".claude",
        )
        assert result == "Part one. Part two."


class TestRecoverLongestFromSession:
    """Tests for _recover_longest_from_session JSONL fallback."""

    def _setup_jsonl(self, tmp_path: Path, session_id: str, lines_data: list[dict]) -> Path:
        """Create a session JSONL with the given lines and return the project dir."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir(exist_ok=True)

        encoded = str(project_dir).replace("/", "-")
        sessions_dir = tmp_path / ".claude" / "projects" / encoded
        sessions_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = sessions_dir / f"{session_id}.jsonl"

        jsonl_path.write_text(
            "\n".join(json.dumps(line) for line in lines_data) + "\n",
            encoding="utf-8",
        )
        return project_dir

    def test_returns_longest_assistant_message(self, tmp_path: Path):
        """Returns the longest assistant text, not the last one."""
        session_id = "test-longest"
        project_dir = self._setup_jsonl(tmp_path, session_id, [
            {"type": "human", "message": {"content": "analyze this"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Short intro."},
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "# Backend Spec\n" + "x" * 2000},
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "The spec is rendered above."},
            ]}},
        ])

        result = ClaudeRunner._recover_longest_from_session(
            session_id, project_dir, claude_dir=tmp_path / ".claude",
        )
        assert result.startswith("# Backend Spec")
        assert len(result) > 2000

    def test_returns_empty_when_file_missing(self, tmp_path: Path):
        """Returns empty string when session JSONL does not exist."""
        result = ClaudeRunner._recover_longest_from_session(
            "nonexistent", tmp_path, claude_dir=tmp_path / ".claude",
        )
        assert result == ""

    def test_returns_empty_when_no_text_blocks(self, tmp_path: Path):
        """Returns empty string when assistant messages have only tool_use blocks."""
        session_id = "test-no-text"
        project_dir = self._setup_jsonl(tmp_path, session_id, [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {}},
            ]}},
        ])

        result = ClaudeRunner._recover_longest_from_session(
            session_id, project_dir, claude_dir=tmp_path / ".claude",
        )
        assert result == ""

    def test_ignores_human_messages(self, tmp_path: Path):
        """Human messages are never returned, even if they are the longest."""
        session_id = "test-ignore-human"
        project_dir = self._setup_jsonl(tmp_path, session_id, [
            {"type": "human", "message": {"content": [
                {"type": "text", "text": "A" * 5000},
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Short reply."},
            ]}},
        ])

        result = ClaudeRunner._recover_longest_from_session(
            session_id, project_dir, claude_dir=tmp_path / ".claude",
        )
        assert result == "Short reply."

    def test_concatenates_multiple_text_blocks(self, tmp_path: Path):
        """Multiple text blocks in one message are concatenated for length comparison."""
        session_id = "test-concat"
        project_dir = self._setup_jsonl(tmp_path, session_id, [
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Part A. "},
                {"type": "tool_use", "name": "Read", "input": {}},
                {"type": "text", "text": "Part B."},
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Short."},
            ]}},
        ])

        result = ClaudeRunner._recover_longest_from_session(
            session_id, project_dir, claude_dir=tmp_path / ".claude",
        )
        assert result == "Part A. Part B."

    def test_single_assistant_message(self, tmp_path: Path):
        """With only one assistant message, returns its text."""
        session_id = "test-single"
        project_dir = self._setup_jsonl(tmp_path, session_id, [
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "The only message."},
            ]}},
        ])

        result = ClaudeRunner._recover_longest_from_session(
            session_id, project_dir, claude_dir=tmp_path / ".claude",
        )
        assert result == "The only message."


class TestRetry:
    """Tests for rate limit retry logic in ClaudeRunner.run()."""

    @staticmethod
    def _make_mock_process(stdout: str, returncode: int = 0, stderr: str = ""):
        return TestRun._make_mock_process(stdout, returncode, stderr)

    async def test_retries_on_rate_limit_then_succeeds(self, tmp_path: Path):
        """First 2 calls rate-limited, 3rd succeeds."""
        runner = ClaudeRunner(max_retries=5, base_delay=30.0, enable_usage_api=False)
        agent = FakeAgentConfig()
        success_json = json.dumps({"result": "done", "total_cost_usd": 0.5})

        fail1 = self._make_mock_process("", returncode=1, stderr="rate limit exceeded")
        fail2 = self._make_mock_process("", returncode=1, stderr="rate limit exceeded")
        success = self._make_mock_process(success_json)

        call_count = 0

        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return fail1 if call_count == 1 else fail2
            return success

        with patch("agentic_dev.claude.runner.asyncio.create_subprocess_exec", side_effect=mock_exec):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await runner.run(agent, "prompt", tmp_path)

        assert result.text == "done"
        assert call_count == 3
        assert mock_sleep.call_count == 2

    async def test_no_retry_on_non_rate_limit_error(self, tmp_path: Path):
        """Non-rate-limit errors raise immediately without retrying."""
        runner = ClaudeRunner(max_retries=5, enable_usage_api=False)
        agent = FakeAgentConfig(name="architect")
        mock_proc = self._make_mock_process("", returncode=1, stderr="Segmentation fault")

        with patch("agentic_dev.claude.runner.asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(AgentRunError, match="architect"):
                await runner.run(agent, "prompt", tmp_path)

    async def test_exhausts_retries_raises_rate_limit_error(self, tmp_path: Path):
        """When all retries are exhausted, raises RateLimitError."""
        runner = ClaudeRunner(max_retries=2, base_delay=10.0, enable_usage_api=False)
        agent = FakeAgentConfig(name="planner")
        mock_proc = self._make_mock_process("", returncode=1, stderr="rate limit exceeded")

        with patch("agentic_dev.claude.runner.asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(RateLimitError) as exc_info:
                    await runner.run(agent, "prompt", tmp_path)

        assert exc_info.value.attempts == 3  # initial + 2 retries

    async def test_session_resume_on_retry(self, tmp_path: Path):
        """Extracts session_id from failed run and uses --resume on retry."""
        runner = ClaudeRunner(max_retries=3, enable_usage_api=False)
        agent = FakeAgentConfig()

        fail_json = json.dumps({"session_id": "sess-abc-123"})
        fail_proc = self._make_mock_process(fail_json, returncode=1, stderr="rate limit exceeded")
        success_json = json.dumps({"result": "ok", "total_cost_usd": 0.1})
        success_proc = self._make_mock_process(success_json)

        call_count = 0
        captured_cmds: list[list[str]] = []

        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            captured_cmds.append(list(args))
            if call_count == 1:
                return fail_proc
            return success_proc

        with patch("agentic_dev.claude.runner.asyncio.create_subprocess_exec", side_effect=mock_exec):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await runner.run(agent, "prompt", tmp_path)

        assert result.text == "ok"
        assert call_count == 2
        # Second call should include --resume with the extracted session_id
        second_cmd = captured_cmds[1]
        assert "--resume" in second_cmd
        resume_idx = second_cmd.index("--resume")
        assert second_cmd[resume_idx + 1] == "sess-abc-123"

    async def test_custom_max_retries(self, tmp_path: Path):
        """max_retries=2 means at most 3 total attempts."""
        runner = ClaudeRunner(max_retries=2, enable_usage_api=False)
        agent = FakeAgentConfig()
        mock_proc = self._make_mock_process("", returncode=1, stderr="429 too many requests")

        call_count = 0

        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return mock_proc

        with patch("agentic_dev.claude.runner.asyncio.create_subprocess_exec", side_effect=mock_exec):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(RateLimitError):
                    await runner.run(agent, "prompt", tmp_path)

        assert call_count == 3  # 1 initial + 2 retries


class TestSessionApiErrorDetection:
    """Tests for ClaudeRunner._session_has_api_error and the resulting retry path.

    Upstream Anthropic 5xx errors surface as exit-1 with empty stderr; the
    error text only lives in the session JSONL as a synthetic assistant
    message marked ``isApiErrorMessage: true``. The runner must detect this
    and retry transparently rather than failing the agent.
    """

    @staticmethod
    def _write_jsonl(
        tmp_path: Path,
        session_id: str,
        entries: list[dict],
    ) -> Path:
        encoded = str(tmp_path).replace("/", "-")
        sessions_dir = tmp_path / ".claude" / "projects" / encoded
        sessions_dir.mkdir(parents=True)
        jsonl_path = sessions_dir / f"{session_id}.jsonl"
        jsonl_path.write_text(
            "\n".join(json.dumps(e) for e in entries), encoding="utf-8"
        )
        return jsonl_path

    def test_detects_api_error_message_marker(self, tmp_path: Path):
        self._write_jsonl(
            tmp_path, "sess-1",
            [
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}},
                {
                    "type": "assistant",
                    "isApiErrorMessage": True,
                    "message": {
                        "content": [{
                            "type": "text",
                            "text": "API Error: {\"type\":\"error\",\"error\":{\"type\":\"api_error\"}}",
                        }],
                    },
                },
            ],
        )
        assert ClaudeRunner._session_has_api_error(
            "sess-1", tmp_path, claude_dir=tmp_path / ".claude",
        ) is True

    def test_returns_false_for_clean_session(self, tmp_path: Path):
        self._write_jsonl(
            tmp_path, "sess-2",
            [
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "result"}]}},
            ],
        )
        assert ClaudeRunner._session_has_api_error(
            "sess-2", tmp_path, claude_dir=tmp_path / ".claude",
        ) is False

    def test_returns_false_when_session_id_missing(self, tmp_path: Path):
        assert ClaudeRunner._session_has_api_error(
            None, tmp_path, claude_dir=tmp_path / ".claude",
        ) is False

    async def test_run_retries_on_transient_api_error_then_succeeds(
        self, tmp_path: Path,
    ):
        """First exit-1 with empty stderr + API error in session → retry → success."""
        runner = ClaudeRunner(max_retries=3, enable_usage_api=False)
        agent = FakeAgentConfig()

        fail_proc = TestRun._make_mock_process(
            json.dumps({"session_id": "sess-api-err"}),
            returncode=1, stderr="",
        )
        success_proc = TestRun._make_mock_process(
            json.dumps({"result": "ok", "total_cost_usd": 0.1}),
        )

        call_count = 0

        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return fail_proc if call_count == 1 else success_proc

        with patch.object(
            ClaudeRunner, "_session_has_api_error", return_value=True,
        ), patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            side_effect=mock_exec,
        ), patch("asyncio.sleep", new_callable=AsyncMock):
            result = await runner.run(agent, "prompt", tmp_path)

        assert result.text == "ok"
        assert call_count == 2

    async def test_run_exhausts_retries_raises_agent_run_error_on_api_error(
        self, tmp_path: Path,
    ):
        """Exhausted retries on API errors raise AgentRunError (not RateLimitError)."""
        runner = ClaudeRunner(max_retries=1, enable_usage_api=False)
        agent = FakeAgentConfig(name="feature_analyst_qa")
        fail_proc = TestRun._make_mock_process(
            json.dumps({"session_id": "sess-x"}), returncode=1, stderr="",
        )

        with patch.object(
            ClaudeRunner, "_session_has_api_error", return_value=True,
        ), patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=fail_proc,
        ), patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(AgentRunError, match="Transient API errors"):
                await runner.run(agent, "prompt", tmp_path)

    async def test_run_discovers_session_when_stdout_has_no_session_id(
        self, tmp_path: Path,
    ):
        """Hard API timeout: exit-1 with empty stdout (no session_id) → discover
        the transcript → detect the api-error → retry by resuming."""
        runner = ClaudeRunner(max_retries=3, enable_usage_api=False)
        agent = FakeAgentConfig()

        fail_proc = TestRun._make_mock_process("", returncode=1, stderr="")
        success_proc = TestRun._make_mock_process(
            json.dumps({"result": "ok", "total_cost_usd": 0.1}),
        )

        call_count = 0

        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return fail_proc if call_count == 1 else success_proc

        with patch.object(
            ClaudeRunner, "_discover_session_id", return_value="discovered-sess",
        ) as disc, patch.object(
            ClaudeRunner, "_session_has_api_error", return_value=True,
        ) as api_err, patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            side_effect=mock_exec,
        ), patch("asyncio.sleep", new_callable=AsyncMock):
            result = await runner.run(agent, "prompt", tmp_path)

        assert result.text == "ok"
        assert call_count == 2  # retried instead of failing
        disc.assert_called()  # fallback used because stdout had no session_id
        # the api-error check (and the resuming retry) got the discovered id
        api_err.assert_called_with("discovered-sess", tmp_path)

    async def test_discovery_fallback_skipped_when_session_id_present(
        self, tmp_path: Path,
    ):
        """When stdout yields a session_id, the discovery fallback is not used."""
        runner = ClaudeRunner(max_retries=1, enable_usage_api=False)
        agent = FakeAgentConfig()
        fail_proc = TestRun._make_mock_process(
            json.dumps({"session_id": "from-stdout"}), returncode=1, stderr="",
        )

        with patch.object(
            ClaudeRunner, "_discover_session_id",
        ) as disc, patch.object(
            ClaudeRunner, "_session_has_api_error", return_value=True,
        ), patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=fail_proc,
        ), patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(AgentRunError, match="Transient API errors"):
                await runner.run(agent, "prompt", tmp_path)

        disc.assert_not_called()


class TestUsageApiFallback:
    """Empty-stderr / unrecognised-error fallback via the usage API.

    When the CLI exits non-zero but stderr does not match any of the
    pattern-based rate-limit signals, the runner should consult the
    Anthropic usage API.  If the API confirms we are over quota, the run
    should be retried like any other rate-limit; otherwise the error
    propagates as a normal AgentRunError.
    """

    @staticmethod
    def _make_mock_process(stdout: str, returncode: int = 0, stderr: str = ""):
        return TestRun._make_mock_process(stdout, returncode, stderr)

    async def test_empty_stderr_with_usage_api_limited_retries(self, tmp_path: Path):
        """Empty stderr + usage API says limited → treat as rate limit and retry."""
        from agentic_dev.claude.rate_limiter import UsageStatus

        runner = ClaudeRunner(max_retries=2, base_delay=1.0)
        agent = FakeAgentConfig()
        success_json = json.dumps({"result": "done", "total_cost_usd": 0.5})

        fail_proc = self._make_mock_process("", returncode=1, stderr="")
        success_proc = self._make_mock_process(success_json)

        call_count = 0

        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return fail_proc if call_count == 1 else success_proc

        limited_status = UsageStatus(five_hour=100.0, is_limited=True)

        with patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            side_effect=mock_exec,
        ):
            with patch.object(
                runner._usage_client, "get_utilization",
                new_callable=AsyncMock, return_value=limited_status,
            ) as mock_get:
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    result = await runner.run(agent, "prompt", tmp_path)

        assert result.text == "done"
        assert call_count == 2
        mock_get.assert_called()  # usage API consulted for fallback

    async def test_empty_stderr_usage_api_says_ok_raises_agent_run_error(
        self, tmp_path: Path,
    ):
        """Empty stderr + usage API healthy → propagate as AgentRunError."""
        from agentic_dev.claude.rate_limiter import UsageStatus

        runner = ClaudeRunner(max_retries=2)
        agent = FakeAgentConfig(name="frontend_developer")
        fail_proc = self._make_mock_process("", returncode=1, stderr="")

        healthy_status = UsageStatus(five_hour=42.0, is_limited=False)

        with patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=fail_proc,
        ):
            with patch.object(
                runner._usage_client, "get_utilization",
                new_callable=AsyncMock, return_value=healthy_status,
            ):
                with pytest.raises(AgentRunError, match="frontend_developer"):
                    await runner.run(agent, "prompt", tmp_path)

    async def test_empty_stderr_without_usage_api_raises_agent_run_error(
        self, tmp_path: Path,
    ):
        """Empty stderr + usage API disabled → propagate as AgentRunError (existing behavior)."""
        runner = ClaudeRunner(max_retries=2, enable_usage_api=False)
        agent = FakeAgentConfig(name="planner")
        fail_proc = self._make_mock_process("", returncode=1, stderr="")

        with patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=fail_proc,
        ):
            with pytest.raises(AgentRunError, match="planner"):
                await runner.run(agent, "prompt", tmp_path)

    async def test_usage_api_failure_falls_through_to_agent_run_error(
        self, tmp_path: Path,
    ):
        """When the usage API itself errors out, we don't loop forever."""
        runner = ClaudeRunner(max_retries=2)
        agent = FakeAgentConfig(name="architect")
        fail_proc = self._make_mock_process("", returncode=1, stderr="")

        with patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=fail_proc,
        ):
            with patch.object(
                runner._usage_client, "get_utilization",
                new_callable=AsyncMock, return_value=None,
            ):
                with pytest.raises(AgentRunError, match="architect"):
                    await runner.run(agent, "prompt", tmp_path)


class TestShortResultRecovery:
    """Tests for the short-result recovery heuristic in ClaudeRunner.run()."""

    @staticmethod
    def _make_mock_process(stdout: str, returncode: int = 0, stderr: str = ""):
        return TestRun._make_mock_process(stdout, returncode, stderr)

    async def test_short_result_replaced_by_longer_session_content(self, tmp_path: Path):
        """When result < 500 chars and session has 5x+ longer content, prefer session."""
        runner = ClaudeRunner(enable_usage_api=False)
        agent = FakeAgentConfig()
        long_spec = "# Backend Spec\n" + "x" * 2000
        output_json = json.dumps({
            "result": "The spec is rendered above.",
            "session_id": "sess-short",
            "total_cost_usd": 0.50,
        })
        mock_process = self._make_mock_process(output_json)

        with patch("agentic_dev.claude.runner.asyncio.create_subprocess_exec", return_value=mock_process):
            with patch.object(
                ClaudeRunner, "_recover_longest_from_session", return_value=long_spec,
            ) as mock_longest:
                result = await runner.run(agent, "prompt", tmp_path)

        mock_longest.assert_called_once_with("sess-short", tmp_path)
        assert result.text == long_spec

    async def test_short_result_kept_when_session_not_much_longer(self, tmp_path: Path):
        """When session content is not 5x longer, keep the original result."""
        runner = ClaudeRunner(enable_usage_api=False)
        agent = FakeAgentConfig()
        output_json = json.dumps({
            "result": "Short but valid output for a detector.",
            "session_id": "sess-keep",
            "total_cost_usd": 0.10,
        })
        mock_process = self._make_mock_process(output_json)

        with patch("agentic_dev.claude.runner.asyncio.create_subprocess_exec", return_value=mock_process):
            with patch.object(
                ClaudeRunner, "_recover_longest_from_session", return_value="Slightly longer text.",
            ) as mock_longest:
                result = await runner.run(agent, "prompt", tmp_path)

        mock_longest.assert_called_once()
        assert result.text == "Short but valid output for a detector."

    async def test_long_result_not_replaced(self, tmp_path: Path):
        """When result >= 500 chars, never attempt longest-text recovery."""
        runner = ClaudeRunner(enable_usage_api=False)
        agent = FakeAgentConfig()
        long_result = "A" * 600
        output_json = json.dumps({
            "result": long_result,
            "session_id": "sess-long",
            "total_cost_usd": 0.20,
        })
        mock_process = self._make_mock_process(output_json)

        with patch("agentic_dev.claude.runner.asyncio.create_subprocess_exec", return_value=mock_process):
            with patch.object(
                ClaudeRunner, "_recover_longest_from_session",
            ) as mock_longest:
                result = await runner.run(agent, "prompt", tmp_path)

        mock_longest.assert_not_called()
        assert result.text == long_result

    async def test_empty_result_prefers_longest_session_text_over_last(
        self, tmp_path: Path,
    ):
        """Empty result: prefer a substantially longer earlier assistant
        message over the final one.

        Regression: previously, an empty result always fell back to
        ``_recover_result_from_session`` (last assistant text). If the agent
        produced a long document and then a short sign-off, the sign-off was
        picked and the real document was silently dropped. The fix checks
        ``_recover_longest_from_session`` first and only falls back to
        last-assistant when no substantially longer message exists.
        """
        runner = ClaudeRunner(enable_usage_api=False)
        agent = FakeAgentConfig()
        long_guide = "# Integration Guide\n" + "y" * 2000
        output_json = json.dumps({
            "result": "",
            "session_id": "sess-empty-longest",
            "total_cost_usd": 0.05,
        })
        mock_process = self._make_mock_process(output_json)

        with patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ), patch.object(
            ClaudeRunner,
            "_recover_longest_from_session",
            return_value=long_guide,
        ) as mock_longest, patch.object(
            ClaudeRunner,
            "_recover_result_from_session",
            return_value="Already verified — all tests pass.",
        ) as mock_last:
            result = await runner.run(agent, "prompt", tmp_path)

        mock_longest.assert_called_once_with("sess-empty-longest", tmp_path)
        mock_last.assert_not_called()
        assert result.text == long_guide

    async def test_empty_result_falls_back_to_last_when_no_longer_message(
        self, tmp_path: Path,
    ):
        """Empty result: when no substantially longer earlier message exists,
        fall back to the last-assistant-text recovery.

        Preserves existing behavior for the common case where the session
        genuinely ends with a short reply.
        """
        runner = ClaudeRunner(enable_usage_api=False)
        agent = FakeAgentConfig()
        output_json = json.dumps({
            "result": "",
            "session_id": "sess-empty-fallback",
            "total_cost_usd": 0.05,
        })
        mock_process = self._make_mock_process(output_json)

        with patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ), patch.object(
            ClaudeRunner,
            "_recover_longest_from_session",
            return_value="",
        ) as mock_longest, patch.object(
            ClaudeRunner,
            "_recover_result_from_session",
            return_value="Recovered last text.",
        ) as mock_last:
            result = await runner.run(agent, "prompt", tmp_path)

        mock_longest.assert_called_once_with("sess-empty-fallback", tmp_path)
        mock_last.assert_called_once_with("sess-empty-fallback", tmp_path)
        assert result.text == "Recovered last text."

    async def test_empty_result_with_short_longest_uses_last_assistant(
        self, tmp_path: Path,
    ):
        """Empty result + short longest (below 1000 chars) → use last-assistant.

        The longest-message heuristic only kicks in when the candidate is
        substantially larger than 1000 chars; otherwise treat it as another
        short reply and fall back to last-assistant, which keeps behaviour
        stable on short sessions.
        """
        runner = ClaudeRunner(enable_usage_api=False)
        agent = FakeAgentConfig()
        output_json = json.dumps({
            "result": "",
            "session_id": "sess-empty-short-longest",
            "total_cost_usd": 0.05,
        })
        mock_process = self._make_mock_process(output_json)

        with patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ), patch.object(
            ClaudeRunner,
            "_recover_longest_from_session",
            return_value="A brief earlier reply.",
        ), patch.object(
            ClaudeRunner,
            "_recover_result_from_session",
            return_value="Final sign-off text.",
        ) as mock_last:
            result = await runner.run(agent, "prompt", tmp_path)

        mock_last.assert_called_once()
        assert result.text == "Final sign-off text."


class TestCollectOutput:
    """Unit tests for _collect_output: collect on CLI *exit*, not pipe-EOF.

    A dev server the agent backgrounds inherits the CLI's stdout/stderr pipes
    and holds them open, so ``communicate`` alone hangs even after the CLI
    exits. ``_collect_output`` keys off ``process.returncode`` and reaps the
    process group to force EOF.
    """

    @staticmethod
    def _proc(returncode=0, pid=4242):
        proc = AsyncMock()
        proc.returncode = returncode
        proc.pid = pid
        return proc

    async def test_normal_completion_returns_output_without_reaping(self, tmp_path: Path):
        proc = self._proc(returncode=0)
        proc.communicate = AsyncMock(return_value=(b"out", b"err"))

        with patch(
            "agentic_dev.claude.runner._terminate_process_group",
            new_callable=AsyncMock,
        ) as reap, patch(
            "agentic_dev.claude.runner._latest_session_activity", return_value=None
        ):
            out, err, wedged = await _collect_output(
                proc, "p", tmp_path, backstop_s=100, idle_timeout_s=100
            )

        assert (out, err, wedged) == (b"out", b"err", None)
        # EOF reached normally: nothing holds the pipe, so no group is reaped.
        reap.assert_not_awaited()

    async def test_leaked_pipe_reaped_then_real_output_returned(self, tmp_path: Path):
        """CLI exited but a child holds the pipe → reap group → real output."""
        release = asyncio.Event()

        async def _comm(*args, **kwargs):
            await release.wait()  # unblocks once the server is reaped
            return (b'{"result": "ok"}', b"")

        proc = self._proc(returncode=0)
        proc.communicate = AsyncMock(side_effect=_comm)

        async def _reap(_process):
            release.set()  # server killed → pipe EOF → communicate completes

        with patch(
            "agentic_dev.claude.runner._terminate_process_group",
            side_effect=_reap,
        ) as reap, patch(
            "agentic_dev.claude.runner._OUTPUT_POLL_INTERVAL_S", 0.01
        ), patch(
            "agentic_dev.claude.runner._latest_session_activity", return_value=None
        ):
            out, err, wedged = await _collect_output(
                proc, "p", tmp_path, backstop_s=100, idle_timeout_s=100
            )

        assert (out, wedged) == (b'{"result": "ok"}', None)
        reap.assert_awaited()

    async def test_detached_child_abandons_stdout(self, tmp_path: Path):
        """A fully-detached child keeps the pipe open → give up on stdout."""
        async def _never(*args, **kwargs):
            await asyncio.Event().wait()

        proc = self._proc(returncode=0)
        proc.communicate = AsyncMock(side_effect=_never)

        with patch(
            "agentic_dev.claude.runner._terminate_process_group",
            new_callable=AsyncMock,
        ), patch(
            "agentic_dev.claude.runner._OUTPUT_POLL_INTERVAL_S", 0.01
        ), patch(
            "agentic_dev.claude.runner._DRAIN_GRACE_S", 0.01
        ), patch(
            "agentic_dev.claude.runner._latest_session_activity", return_value=None
        ):
            out, err, wedged = await _collect_output(
                proc, "p", tmp_path, backstop_s=100, idle_timeout_s=100
            )

        assert (out, err, wedged) == (b"", b"", None)

    async def test_backstop_fires_when_cli_never_exits(self, tmp_path: Path):
        """idle window high → the absolute backstop is what fires."""
        async def _never(*args, **kwargs):
            await asyncio.Event().wait()

        proc = self._proc(returncode=None)  # process never exits
        proc.communicate = AsyncMock(side_effect=_never)

        with patch(
            "agentic_dev.claude.runner._terminate_process_group",
            new_callable=AsyncMock,
        ) as reap, patch(
            "agentic_dev.claude.runner._OUTPUT_POLL_INTERVAL_S", 0.01
        ), patch(
            "agentic_dev.claude.runner._latest_session_activity", return_value=None
        ):
            out, err, wedged = await _collect_output(
                proc, "p", tmp_path, backstop_s=0.05, idle_timeout_s=100
            )

        assert wedged is not None and "did not exit" in wedged.reason
        reap.assert_awaited()

    async def test_idle_wedge_when_transcript_stops_advancing(self, tmp_path: Path):
        """Alive CLI whose transcript freezes → idle wedge (before the backstop)."""
        async def _never(*args, **kwargs):
            await asyncio.Event().wait()

        proc = self._proc(returncode=None)
        proc.communicate = AsyncMock(side_effect=_never)

        with patch(
            "agentic_dev.claude.runner._terminate_process_group",
            new_callable=AsyncMock,
        ) as reap, patch(
            "agentic_dev.claude.runner._OUTPUT_POLL_INTERVAL_S", 0.01
        ), patch(
            "agentic_dev.claude.runner._latest_session_activity", return_value=1000.0
        ):
            # constant (stale) activity, small idle, large backstop
            out, err, wedged = await _collect_output(
                proc, "p", tmp_path, backstop_s=100, idle_timeout_s=0.05
            )

        assert wedged is not None and "progress" in wedged.reason
        reap.assert_awaited()

    async def test_progress_resets_idle_timer(self, tmp_path: Path):
        """While the transcript keeps advancing, the idle timer never fires."""
        async def _never(*args, **kwargs):
            await asyncio.Event().wait()

        proc = self._proc(returncode=None)
        proc.communicate = AsyncMock(side_effect=_never)

        counter = {"t": 1000.0}

        def _advancing(*args, **kwargs):
            counter["t"] += 1.0  # transcript always growing
            return counter["t"]

        with patch(
            "agentic_dev.claude.runner._terminate_process_group",
            new_callable=AsyncMock,
        ), patch(
            "agentic_dev.claude.runner._OUTPUT_POLL_INTERVAL_S", 0.01
        ), patch(
            "agentic_dev.claude.runner._latest_session_activity",
            side_effect=_advancing,
        ):
            # idle window tiny, but continuous progress keeps resetting it, so the
            # absolute backstop is what eventually fires — not the idle path.
            out, err, wedged = await _collect_output(
                proc, "p", tmp_path, backstop_s=0.1, idle_timeout_s=0.02
            )

        assert wedged is not None and "did not exit" in wedged.reason


class TestLatestSessionActivity:
    """Unit tests for the transcript-heartbeat helper."""

    def test_returns_newest_jsonl_mtime(self, tmp_path: Path):
        import os
        wd = tmp_path / "proj"
        wd.mkdir()
        claude_dir = tmp_path / "claude"
        sessions = claude_dir / "projects" / str(wd).replace("/", "-")
        sessions.mkdir(parents=True)
        (sessions / "a.jsonl").write_text("{}")
        (sessions / "b.jsonl").write_text("{}")
        os.utime(sessions / "a.jsonl", (1000, 1000))
        os.utime(sessions / "b.jsonl", (2000, 2000))

        assert _latest_session_activity(wd, claude_dir=claude_dir) == 2000.0

    def test_none_when_no_session_dir(self, tmp_path: Path):
        assert (
            _latest_session_activity(tmp_path / "nope", claude_dir=tmp_path / "claude")
            is None
        )


class TestStallDiagnostics:
    """Live process snapshot + transcript classification captured on a wedge."""

    def test_snapshot_includes_own_group(self):
        snap = _snapshot_process_group(os.getpgrp())
        assert str(os.getpid()) in snap

    def test_snapshot_bogus_pgid_does_not_raise(self):
        snap = _snapshot_process_group(2_000_000_000)
        assert isinstance(snap, str)
        assert "no live members" in snap

    @staticmethod
    def _write_transcript(claude_dir: Path, working_dir: Path, entries: list[dict]):
        encoded = str(working_dir).replace("/", "-")
        sessions = claude_dir / "projects" / encoded
        sessions.mkdir(parents=True)
        (sessions / "sess.jsonl").write_text(
            "\n".join(json.dumps(e) for e in entries)
        )

    def test_diagnose_tool_hang(self, tmp_path: Path):
        wd = Path("/work/x")
        self._write_transcript(tmp_path, wd, [
            {"type": "assistant", "timestamp": "T1",
             "message": {"content": [{"type": "text", "text": "ok"}]}},
            {"type": "assistant", "timestamp": "T2",
             "message": {"content": [{"type": "tool_use", "name": "browser_click"}]}},
        ])
        summary, tail = ClaudeRunner._diagnose_stall(
            wd, "sess", datetime(2026, 1, 1, tzinfo=timezone.utc), claude_dir=tmp_path
        )
        assert "tool hang" in summary and "browser_click" in summary
        assert "tool_use:browser_click" in tail

    def test_diagnose_model_stall(self, tmp_path: Path):
        wd = Path("/work/y")
        self._write_transcript(tmp_path, wd, [
            {"type": "assistant", "timestamp": "T1",
             "message": {"content": [{"type": "tool_use", "name": "browser_snapshot"}]}},
            {"type": "user", "timestamp": "T2",
             "message": {"content": [{"type": "tool_result", "tool_use_id": "x"}]}},
        ])
        summary, _ = ClaudeRunner._diagnose_stall(
            wd, "sess", datetime(2026, 1, 1, tzinfo=timezone.utc), claude_dir=tmp_path
        )
        assert "model stall" in summary and "browser_snapshot" in summary

    def test_diagnose_missing_transcript(self, tmp_path: Path):
        summary, tail = ClaudeRunner._diagnose_stall(
            Path("/work/z"), None,
            datetime(2026, 1, 1, tzinfo=timezone.utc), claude_dir=tmp_path,
        )
        assert "unavailable" in summary


class TestRunOutputCollection:
    """run() wiring of the exit-keyed collector, backstop, and salvage."""

    @staticmethod
    def _hanging_proc(returncode=None, pid=4242):
        proc = AsyncMock()

        async def _never(*args, **kwargs):
            await asyncio.Event().wait()

        proc.communicate = AsyncMock(side_effect=_never)
        proc.returncode = returncode
        proc.pid = pid
        return proc

    async def test_wedged_cli_raises_agent_run_error(self, tmp_path: Path):
        runner = ClaudeRunner(enable_usage_api=False)
        agent = FakeAgentConfig(name="uat_api", timeout_s=0.05)
        proc = self._hanging_proc(returncode=None)

        with patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=proc,
        ) as mock_exec, patch(
            "agentic_dev.claude.runner._terminate_process_group",
            new_callable=AsyncMock,
        ) as reap, patch(
            "agentic_dev.claude.runner._OUTPUT_POLL_INTERVAL_S", 0.01
        ):
            with pytest.raises(AgentRunError, match="did not exit") as exc_info:
                await runner.run(agent, "prompt", tmp_path)

        assert exc_info.value.agent_name == "uat_api"
        reap.assert_awaited()  # process group killed
        mock_exec.assert_called_once()  # a wedge is a hard fail — no retry

    async def test_subprocess_started_in_new_session(self, tmp_path: Path):
        """Each subprocess leads its own session so the group can be reaped."""
        runner = ClaudeRunner(enable_usage_api=False)
        agent = FakeAgentConfig()
        proc = TestRun._make_mock_process(
            json.dumps({"result": "ok", "total_cost_usd": 0.01})
        )

        with patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=proc,
        ) as mock_exec:
            await runner.run(agent, "prompt", tmp_path)

        assert mock_exec.call_args.kwargs["start_new_session"] is True

    async def test_default_and_override_timeouts(self, tmp_path: Path):
        captured: dict[str, float] = {}

        async def fake_collect(process, prompt, working_dir, backstop_s, idle_timeout_s):
            captured["backstop"] = backstop_s
            captured["idle"] = idle_timeout_s
            return (json.dumps({"result": "ok"}).encode(), b"", None)

        runner = ClaudeRunner(enable_usage_api=False)
        proc = AsyncMock()
        proc.returncode = 0
        proc.pid = 1

        with patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=proc,
        ), patch(
            "agentic_dev.claude.runner._collect_output", side_effect=fake_collect
        ):
            await runner.run(FakeAgentConfig(), "p", tmp_path)
            assert captured["backstop"] == DEFAULT_AGENT_BACKSTOP_S
            assert captured["idle"] == DEFAULT_AGENT_IDLE_TIMEOUT_S

            await runner.run(
                FakeAgentConfig(timeout_s=123, idle_timeout_s=45), "p", tmp_path
            )
            assert captured["backstop"] == 123
            assert captured["idle"] == 45

    async def test_unreadable_stdout_recovers_from_transcript(self, tmp_path: Path):
        """Detached child held the pipe → empty stdout → salvage from transcript."""
        runner = ClaudeRunner(enable_usage_api=False)
        agent = FakeAgentConfig()
        proc = AsyncMock()
        proc.returncode = 0
        proc.pid = 1

        with patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=proc,
        ), patch(
            "agentic_dev.claude.runner._collect_output",
            new_callable=AsyncMock,
            return_value=(b"", b"", None),
        ), patch.object(
            ClaudeRunner, "_discover_session_id", return_value="sess-x"
        ), patch.object(
            ClaudeRunner, "_recover_longest_from_session", return_value="Recovered doc.",
        ):
            result = await runner.run(agent, "prompt", tmp_path)

        assert result.text == "Recovered doc."
        assert result.session_id == "sess-x"

    async def test_idle_wedged_cli_raises_agent_run_error(self, tmp_path: Path):
        """An alive-but-stalled CLI (no transcript progress) fails cleanly."""
        runner = ClaudeRunner(enable_usage_api=False)
        agent = FakeAgentConfig(name="uat_web", idle_timeout_s=0.05)
        proc = self._hanging_proc(returncode=None)

        with patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=proc,
        ) as mock_exec, patch(
            "agentic_dev.claude.runner._terminate_process_group",
            new_callable=AsyncMock,
        ) as reap, patch(
            "agentic_dev.claude.runner._OUTPUT_POLL_INTERVAL_S", 0.01
        ), patch(
            "agentic_dev.claude.runner._latest_session_activity", return_value=1000.0
        ):
            with pytest.raises(AgentRunError, match="made no progress") as exc_info:
                await runner.run(agent, "prompt", tmp_path)

        assert exc_info.value.agent_name == "uat_web"
        reap.assert_awaited()  # process group killed
        mock_exec.assert_called_once()  # a wedge is a hard fail — no retry

    async def test_idle_wedge_writes_stall_report(self, tmp_path: Path):
        runner = ClaudeRunner(enable_usage_api=False, log_dir=tmp_path)
        agent = FakeAgentConfig(name="uat_web", idle_timeout_s=0.05)
        proc = self._hanging_proc(returncode=None)

        with patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=proc,
        ), patch(
            "agentic_dev.claude.runner._terminate_process_group",
            new_callable=AsyncMock,
        ), patch(
            "agentic_dev.claude.runner._OUTPUT_POLL_INTERVAL_S", 0.01
        ), patch(
            "agentic_dev.claude.runner._latest_session_activity", return_value=1000.0
        ), patch(
            "agentic_dev.claude.runner._snapshot_process_group", return_value="SNAP"
        ):
            with pytest.raises(AgentRunError):
                await runner.run(agent, "prompt", tmp_path)

        reports = list((tmp_path / "stalls").glob("uat_web_*.md"))
        assert len(reports) == 1
        assert "SNAP" in reports[0].read_text()

    async def test_diagnostics_failure_does_not_mask_wedge(self, tmp_path: Path):
        """If diagnosis blows up, the wedge still fails cleanly (no hang/crash)."""
        runner = ClaudeRunner(enable_usage_api=False, log_dir=tmp_path)
        agent = FakeAgentConfig(name="uat_web", idle_timeout_s=0.05)
        proc = self._hanging_proc(returncode=None)

        with patch(
            "agentic_dev.claude.runner.asyncio.create_subprocess_exec",
            return_value=proc,
        ), patch(
            "agentic_dev.claude.runner._terminate_process_group",
            new_callable=AsyncMock,
        ), patch(
            "agentic_dev.claude.runner._OUTPUT_POLL_INTERVAL_S", 0.01
        ), patch(
            "agentic_dev.claude.runner._latest_session_activity", return_value=1000.0
        ), patch(
            "agentic_dev.claude.runner._snapshot_process_group", return_value="SNAP"
        ), patch.object(
            ClaudeRunner, "_diagnose_stall", side_effect=RuntimeError("boom")
        ):
            with pytest.raises(AgentRunError, match="made no progress"):
                await runner.run(agent, "prompt", tmp_path)


class TestTerminateProcessGroup:
    """Reaper signals the group by pid (start_new_session ⇒ pgid == pid),
    which stays valid even after the CLI leader itself has exited."""

    async def test_sigterm_then_empty_group_no_sigkill(self):
        proc = AsyncMock()
        proc.pid = 4242

        def killpg(pgid, sig):
            if sig == 0:
                raise ProcessLookupError  # group emptied after SIGTERM

        with patch(
            "agentic_dev.claude.runner.os.killpg", side_effect=killpg
        ) as kp, patch(
            "agentic_dev.claude.runner.asyncio.sleep", new_callable=AsyncMock
        ):
            await _terminate_process_group(proc)

        sigs = [c.args[1] for c in kp.call_args_list]
        assert signal.SIGTERM in sigs
        assert signal.SIGKILL not in sigs

    async def test_sigkill_when_group_survives_grace(self):
        proc = AsyncMock()
        proc.pid = 4242

        with patch("agentic_dev.claude.runner.os.killpg") as kp, patch(
            "agentic_dev.claude.runner.asyncio.sleep", new_callable=AsyncMock
        ), patch("agentic_dev.claude.runner._PROCESS_GROUP_TERM_GRACE_S", 0.4):
            await _terminate_process_group(proc)

        sigs = [c.args[1] for c in kp.call_args_list]
        assert sigs[0] == signal.SIGTERM
        assert sigs[-1] == signal.SIGKILL

    async def test_already_empty_group_is_a_noop(self):
        proc = AsyncMock()
        proc.pid = 4242

        with patch(
            "agentic_dev.claude.runner.os.killpg", side_effect=ProcessLookupError
        ) as kp, patch(
            "agentic_dev.claude.runner.asyncio.sleep", new_callable=AsyncMock
        ):
            await _terminate_process_group(proc)

        kp.assert_called_once_with(4242, signal.SIGTERM)
