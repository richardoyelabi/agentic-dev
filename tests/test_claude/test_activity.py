"""Tests for live transcript-activity parsing and tailing."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agentic_dev.claude.activity import (
    Activity,
    discover_latest_session_id,
    format_tool_use,
    iter_content_blocks,
    sessions_dir_for,
    summarize_transcript_line,
    tail_transcript_activity,
    transcript_path,
)
from agentic_dev.logging.events import AgentActivityEvent


# ---------------------------------------------------------------------------
# Session-path helpers
# ---------------------------------------------------------------------------


class TestSessionPathHelpers:
    def test_sessions_dir_encodes_working_dir(self, tmp_path: Path) -> None:
        wd = Path("/home/me/proj")
        claude_dir = tmp_path / ".claude"
        result = sessions_dir_for(wd, claude_dir)
        assert result == claude_dir / "projects" / "-home-me-proj"

    def test_transcript_path_appends_session_jsonl(self, tmp_path: Path) -> None:
        wd = Path("/home/me/proj")
        claude_dir = tmp_path / ".claude"
        result = transcript_path(wd, "abc123", claude_dir)
        assert result == claude_dir / "projects" / "-home-me-proj" / "abc123.jsonl"

    def test_discover_returns_none_when_dir_missing(self, tmp_path: Path) -> None:
        result = discover_latest_session_id(
            Path("/nope"), datetime.now(timezone.utc), tmp_path / ".claude"
        )
        assert result is None

    def test_discover_returns_newest_session_after_start(self, tmp_path: Path) -> None:
        wd = tmp_path / "proj"
        claude_dir = tmp_path / ".claude"
        sdir = sessions_dir_for(wd, claude_dir)
        sdir.mkdir(parents=True)
        old = sdir / "old.jsonl"
        new = sdir / "new.jsonl"
        old.write_text("{}\n", encoding="utf-8")
        new.write_text("{}\n", encoding="utf-8")
        now = datetime.now(timezone.utc).timestamp()
        os.utime(old, (now - 100, now - 100))
        os.utime(new, (now - 10, now - 10))
        start = datetime.now(timezone.utc) - timedelta(seconds=200)
        assert discover_latest_session_id(wd, start, claude_dir) == "new"

    def test_discover_ignores_transcripts_before_start(self, tmp_path: Path) -> None:
        wd = tmp_path / "proj"
        claude_dir = tmp_path / ".claude"
        sdir = sessions_dir_for(wd, claude_dir)
        sdir.mkdir(parents=True)
        stale = sdir / "stale.jsonl"
        stale.write_text("{}\n", encoding="utf-8")
        now = datetime.now(timezone.utc).timestamp()
        os.utime(stale, (now - 1000, now - 1000))
        start = datetime.now(timezone.utc)  # everything older than start
        assert discover_latest_session_id(wd, start, claude_dir) is None


# ---------------------------------------------------------------------------
# Content-block extraction (shared with stall diagnosis)
# ---------------------------------------------------------------------------


class TestIterContentBlocks:
    def test_list_content_returns_block_dicts(self) -> None:
        msg = {"message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {}},
            {"type": "text", "text": "hi"},
        ]}}
        blocks = iter_content_blocks(msg)
        assert [b["type"] for b in blocks] == ["tool_use", "text"]

    def test_string_content_becomes_text_block(self) -> None:
        msg = {"message": {"content": "just a string"}}
        assert iter_content_blocks(msg) == [{"type": "text", "text": "just a string"}]

    def test_missing_content_returns_empty(self) -> None:
        assert iter_content_blocks({"message": {}}) == []
        assert iter_content_blocks({}) == []

    def test_non_dict_blocks_filtered_out(self) -> None:
        msg = {"message": {"content": ["bad", {"type": "text", "text": "ok"}]}}
        blocks = iter_content_blocks(msg)
        assert blocks == [{"type": "text", "text": "ok"}]


# ---------------------------------------------------------------------------
# Tool-use formatting
# ---------------------------------------------------------------------------


class TestFormatToolUse:
    @pytest.mark.parametrize("name", ["Read", "Edit", "Write", "NotebookEdit"])
    def test_file_tools_show_basename(self, name: str) -> None:
        key = "notebook_path" if name == "NotebookEdit" else "file_path"
        result = format_tool_use(name, {key: "/a/b/routes.py"})
        assert result == f"{name} routes.py"

    def test_bash_shows_truncated_command(self) -> None:
        result = format_tool_use("Bash", {"command": "pytest -q tests/ && ruff check ."})
        assert result.startswith("Bash pytest -q")

    def test_bash_collapses_whitespace(self) -> None:
        result = format_tool_use("Bash", {"command": "echo\n  hello\n  world"})
        assert "\n" not in result

    def test_grep_shows_quoted_pattern(self) -> None:
        assert format_tool_use("Grep", {"pattern": "class .*Config"}) == 'Grep "class .*Config"'

    def test_glob_shows_pattern(self) -> None:
        assert format_tool_use("Glob", {"pattern": "**/*.py"}) == "Glob **/*.py"

    def test_task_prefers_subagent_type(self) -> None:
        result = format_tool_use("Task", {"subagent_type": "code-reviewer", "description": "x"})
        assert result == "Task code-reviewer"

    def test_task_falls_back_to_description(self) -> None:
        assert format_tool_use("Task", {"description": "explore code"}) == "Task explore code"

    def test_websearch_shows_query(self) -> None:
        assert format_tool_use("WebSearch", {"query": "rich live"}) == 'WebSearch "rich live"'

    def test_mcp_tool_uses_short_name(self) -> None:
        assert format_tool_use("mcp__figma__get_node", {}) == "get_node"

    def test_unknown_tool_returns_name(self) -> None:
        assert format_tool_use("TodoWrite", {}) == "TodoWrite"

    def test_missing_input_does_not_crash(self) -> None:
        assert format_tool_use("Edit", {}) == "Edit"


# ---------------------------------------------------------------------------
# Per-line summarization
# ---------------------------------------------------------------------------


def _assistant(blocks: list[dict]) -> str:
    return json.dumps({"type": "assistant", "message": {"content": blocks}})


class TestSummarizeTranscriptLine:
    def test_tool_use_is_summarized(self) -> None:
        line = _assistant([{"type": "tool_use", "name": "Edit", "input": {"file_path": "/x/a.py"}}])
        result = summarize_transcript_line(line)
        assert result == Activity(text="Edit a.py", tool="Edit")

    def test_text_only_is_writing_marker(self) -> None:
        line = _assistant([{"type": "text", "text": "Let me plan this out."}])
        result = summarize_transcript_line(line)
        assert result == Activity(text="writing…", tool=None)

    def test_tool_use_wins_over_text(self) -> None:
        line = _assistant([
            {"type": "text", "text": "I will edit the file"},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/x/a.py"}},
        ])
        assert summarize_transcript_line(line).text == "Edit a.py"

    def test_last_tool_use_wins(self) -> None:
        line = _assistant([
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/x/a.py"}},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/x/b.py"}},
        ])
        assert summarize_transcript_line(line).text == "Edit b.py"

    def test_user_message_is_skipped(self) -> None:
        line = json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "ok"}]}})
        assert summarize_transcript_line(line) is None

    def test_system_message_is_skipped(self) -> None:
        line = json.dumps({"type": "system", "message": {"content": "init"}})
        assert summarize_transcript_line(line) is None

    def test_empty_text_block_is_skipped(self) -> None:
        line = _assistant([{"type": "text", "text": "   "}])
        assert summarize_transcript_line(line) is None

    def test_malformed_json_returns_none(self) -> None:
        assert summarize_transcript_line("{not json") is None

    def test_non_object_json_returns_none(self) -> None:
        assert summarize_transcript_line("123") is None
        assert summarize_transcript_line("[]") is None


# ---------------------------------------------------------------------------
# Live tailing
# ---------------------------------------------------------------------------


def _collector() -> tuple[list[AgentActivityEvent], object]:
    events: list[AgentActivityEvent] = []

    def emit_fn(_logger: logging.Logger, event: AgentActivityEvent) -> None:
        events.append(event)

    return events, emit_fn


class TestTailTranscriptActivity:
    async def test_emits_events_for_appended_actions(self, tmp_path: Path) -> None:
        wd = tmp_path / "proj"
        claude_dir = tmp_path / ".claude"
        path = transcript_path(wd, "sid1", claude_dir)
        path.parent.mkdir(parents=True)
        path.write_text("", encoding="utf-8")

        events, emit_fn = _collector()
        task = asyncio.create_task(tail_transcript_activity(
            wd, datetime.now(timezone.utc) - timedelta(seconds=60), "sid1",
            "developer", 2, logger=logging.getLogger("test"),
            claude_dir=claude_dir, poll_interval=0.01, emit_fn=emit_fn,
        ))
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(_assistant([{"type": "tool_use", "name": "Edit",
                                     "input": {"file_path": "/x/routes.py"}}]) + "\n")
            await asyncio.sleep(0.1)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert len(events) == 1
        assert events[0].agent_name == "developer"
        assert events[0].activity == "Edit routes.py"
        assert events[0].tool == "Edit"
        assert events[0].sprint == 2

    async def test_buffers_partial_trailing_line(self, tmp_path: Path) -> None:
        wd = tmp_path / "proj"
        claude_dir = tmp_path / ".claude"
        path = transcript_path(wd, "sid1", claude_dir)
        path.parent.mkdir(parents=True)
        path.write_text("", encoding="utf-8")

        events, emit_fn = _collector()
        task = asyncio.create_task(tail_transcript_activity(
            wd, datetime.now(timezone.utc) - timedelta(seconds=60), "sid1",
            "developer", None, logger=logging.getLogger("test"),
            claude_dir=claude_dir, poll_interval=0.01, emit_fn=emit_fn,
        ))
        try:
            full = _assistant([{"type": "tool_use", "name": "Read",
                                "input": {"file_path": "/x/a.py"}}])
            with path.open("a", encoding="utf-8") as f:
                f.write(full[:10])  # partial, no newline
            await asyncio.sleep(0.05)
            assert events == []  # nothing emitted for an incomplete line
            with path.open("a", encoding="utf-8") as f:
                f.write(full[10:] + "\n")  # complete the line
            await asyncio.sleep(0.05)
            assert len(events) == 1
            assert events[0].activity == "Read a.py"
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_discovers_transcript_when_session_id_unknown(self, tmp_path: Path) -> None:
        wd = tmp_path / "proj"
        claude_dir = tmp_path / ".claude"
        sdir = sessions_dir_for(wd, claude_dir)
        sdir.mkdir(parents=True)

        events, emit_fn = _collector()
        task = asyncio.create_task(tail_transcript_activity(
            wd, datetime.now(timezone.utc) - timedelta(seconds=60), None,
            "architect", None, logger=logging.getLogger("test"),
            claude_dir=claude_dir, poll_interval=0.01, emit_fn=emit_fn,
        ))
        try:
            # Transcript appears only after the tailer has started polling.
            await asyncio.sleep(0.03)
            path = sdir / "late.jsonl"
            path.write_text(_assistant([{"type": "tool_use", "name": "Bash",
                                         "input": {"command": "ls"}}]) + "\n",
                            encoding="utf-8")
            await asyncio.sleep(0.1)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert [e.activity for e in events] == ["Bash ls"]

    @pytest.mark.timeout(5)
    async def test_polling_survives_globally_patched_asyncio_sleep(
        self, tmp_path: Path
    ) -> None:
        """The poll heartbeat must not use the global ``asyncio.sleep``.

        The runner's retry tests patch ``asyncio.sleep`` to skip backoff waits.
        If the tailer used that patched (non-yielding) sleep, its loop would
        busy-spin and starve the event loop. The transcript never appears here,
        so the tailer keeps polling; it must still yield and stay cancellable.
        """
        real_sleep = asyncio.sleep
        wd = tmp_path / "proj"
        claude_dir = tmp_path / ".claude"
        sessions_dir_for(wd, claude_dir).mkdir(parents=True)  # empty -> keeps polling

        with patch("asyncio.sleep", new_callable=AsyncMock):
            task = asyncio.create_task(tail_transcript_activity(
                wd, datetime.now(timezone.utc) - timedelta(seconds=60), None,
                "architect", None, logger=logging.getLogger("test"),
                claude_dir=claude_dir, poll_interval=0.01,
            ))
            # Would never return if the tailer busy-loops on the patched sleep.
            await real_sleep(0.05)
            assert not task.done()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    async def test_cancellation_is_clean(self, tmp_path: Path) -> None:
        wd = tmp_path / "proj"
        claude_dir = tmp_path / ".claude"
        task = asyncio.create_task(tail_transcript_activity(
            wd, datetime.now(timezone.utc), None, "architect", None,
            logger=logging.getLogger("test"), claude_dir=claude_dir,
            poll_interval=0.01,
        ))
        await asyncio.sleep(0.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
