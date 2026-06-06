"""Live agent-activity parsing from Claude CLI session transcripts.

The Claude CLI appends a session transcript
(``~/.claude/projects/<encoded>/<session_id>.jsonl``) as an agent works. This
module turns that transcript into a concise, human-readable feed of what the
agent is doing *right now*, so the dashboard can show in-progress actions
between ``agent_start`` and ``agent_complete``.

It owns the session-path helpers (shared with the runner) and deliberately does
not import the runner, keeping the dependency one-directional
(``runner -> activity``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from agentic_dev.logging import emit
from agentic_dev.logging.events import AgentActivityEvent, LogEvent

# Bound at import so the tailer's poll heartbeat is independent of the global
# ``asyncio.sleep`` — the runner's retry path mocks that away in tests to skip
# backoff waits, which would otherwise turn this module's poll loop into a
# busy-spin that starves the event loop.
_sleep = asyncio.sleep


# ---------------------------------------------------------------------------
# Session-transcript paths (shared with the runner)
# ---------------------------------------------------------------------------


def _claude_dir(claude_dir: Path | None) -> Path:
    return claude_dir if claude_dir is not None else Path.home() / ".claude"


def sessions_dir_for(working_dir: Path, claude_dir: Path | None = None) -> Path:
    """Directory holding session transcripts for ``working_dir``."""
    encoded = str(working_dir).replace("/", "-")
    return _claude_dir(claude_dir) / "projects" / encoded


def transcript_path(
    working_dir: Path, session_id: str, claude_dir: Path | None = None
) -> Path:
    """Path to the ``<session_id>.jsonl`` transcript for ``working_dir``."""
    return sessions_dir_for(working_dir, claude_dir) / f"{session_id}.jsonl"


def discover_latest_session_id(
    working_dir: Path, start_time: datetime, claude_dir: Path | None = None
) -> str | None:
    """Newest transcript id for ``working_dir`` written at/after ``start_time``.

    Agents run sequentially per working dir, so the most recently modified
    ``*.jsonl`` is unambiguous. A small slack absorbs clock granularity.
    """
    sessions_dir = sessions_dir_for(working_dir, claude_dir)
    if not sessions_dir.is_dir():
        return None
    cutoff = start_time.timestamp() - 5.0
    newest: tuple[float, str] | None = None
    for path in sessions_dir.glob("*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        if newest is None or mtime > newest[0]:
            newest = (mtime, path.stem)
    return newest[1] if newest else None


# ---------------------------------------------------------------------------
# Transcript content parsing
# ---------------------------------------------------------------------------


def iter_content_blocks(msg: dict) -> list[dict]:
    """Return the content blocks of a transcript message as dicts.

    ``message.content`` is either a list of block dicts or a bare string
    (treated as one text block). Non-dict entries are dropped. Shared with the
    runner's stall diagnosis so both read the transcript the same way.
    """
    message = msg.get("message") if isinstance(msg, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return []


_FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"}
_MAX_LEN = 48


def _truncate(text: str, limit: int = _MAX_LEN) -> str:
    """Collapse whitespace and cap length with an ellipsis."""
    collapsed = " ".join(str(text).split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


def format_tool_use(name: str, tool_input: dict) -> str:
    """Render a concise phrase for a ``tool_use`` block."""
    if not isinstance(tool_input, dict):
        tool_input = {}

    if name in _FILE_TOOLS:
        target = tool_input.get("file_path") or tool_input.get("notebook_path")
        return f"{name} {Path(target).name}" if target else name
    if name == "Bash":
        cmd = tool_input.get("command")
        return f"Bash {_truncate(cmd)}" if cmd else "Bash"
    if name == "Grep":
        pattern = tool_input.get("pattern")
        return f'Grep "{_truncate(pattern)}"' if pattern else "Grep"
    if name == "Glob":
        pattern = tool_input.get("pattern")
        return f"Glob {_truncate(pattern)}" if pattern else "Glob"
    if name == "Task":
        label = tool_input.get("subagent_type") or tool_input.get("description")
        return f"Task {_truncate(label)}" if label else "Task"
    if name == "WebSearch":
        query = tool_input.get("query")
        return f'WebSearch "{_truncate(query)}"' if query else "WebSearch"
    if name == "WebFetch":
        url = tool_input.get("url")
        return f"WebFetch {_truncate(url)}" if url else "WebFetch"
    if name.startswith("mcp__"):
        return name.split("__")[-1]
    return name


@dataclass(frozen=True)
class Activity:
    """A single summarized agent action."""

    text: str
    tool: str | None = None


def summarize_transcript_line(raw: str) -> Activity | None:
    """Summarize one transcript JSONL line into a single activity, or ``None``.

    Only assistant messages are summarized (what the agent itself initiates). A
    ``tool_use`` block wins over prose; a non-empty text block becomes a brief
    ``writing…`` marker. Tool results, user/system turns, and malformed lines
    yield ``None``.
    """
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(msg, dict) or msg.get("type") != "assistant":
        return None

    tool_activity: Activity | None = None
    has_text = False
    for block in iter_content_blocks(msg):
        btype = block.get("type")
        if btype == "tool_use":
            name = block.get("name") or ""
            tool_activity = Activity(
                text=format_tool_use(name, block.get("input") or {}),
                tool=name or None,
            )
        elif btype == "text" and str(block.get("text") or "").strip():
            has_text = True

    if tool_activity is not None:
        return tool_activity
    if has_text:
        return Activity(text="writing…")
    return None


# ---------------------------------------------------------------------------
# Live tailing
# ---------------------------------------------------------------------------


EmitFn = Callable[[logging.Logger, LogEvent], None]


def _locate_transcript(
    working_dir: Path,
    session_id: str | None,
    start_time: datetime,
    claude_dir: Path | None,
) -> Path | None:
    sid = session_id or discover_latest_session_id(working_dir, start_time, claude_dir)
    if not sid:
        return None
    path = transcript_path(working_dir, sid, claude_dir)
    return path if path.exists() else None


async def tail_transcript_activity(
    working_dir: Path,
    start_time: datetime,
    session_id: str | None,
    agent_name: str,
    sprint: int | None,
    *,
    logger: logging.Logger,
    claude_dir: Path | None = None,
    poll_interval: float = 0.5,
    emit_fn: EmitFn = emit,
) -> None:
    """Follow an agent's live transcript, emitting one event per new action.

    Read-only on the transcript, so it never interferes with the runner's
    transcript-inactivity wedge detection. Locates the transcript (by
    ``session_id`` when resuming, else the newest for ``working_dir``), then
    polls for appended lines until cancelled. Complete lines only are parsed;
    a partial trailing line is buffered until its newline arrives.
    """
    path: Path | None = None
    offset = 0
    buffer = b""

    while True:
        if path is None:
            path = _locate_transcript(working_dir, session_id, start_time, claude_dir)

        if path is not None:
            try:
                with path.open("rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
                    offset = handle.tell()
            except OSError:
                chunk = b""

            if chunk:
                buffer += chunk
                lines = buffer.split(b"\n")
                buffer = lines.pop()  # retain any incomplete trailing line
                for raw in lines:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    activity = summarize_transcript_line(line)
                    if activity is not None:
                        emit_fn(logger, AgentActivityEvent(
                            agent_name=agent_name,
                            tool=activity.tool,
                            activity=activity.text,
                            sprint=sprint,
                            message=activity.text,
                        ))

        await _sleep(poll_interval)
