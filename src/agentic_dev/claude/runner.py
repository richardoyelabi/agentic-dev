"""Async subprocess wrapper for invoking Claude Code CLI."""

import asyncio
import contextlib
import json
import os
import signal
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from agentic_dev.claude.activity import (
    iter_content_blocks,
    tail_transcript_activity,
)
from agentic_dev.claude.rate_limiter import (
    RateLimitDetector,
    UsageApiClient,
    WaitStrategy,
)
from agentic_dev.config import (
    DEFAULT_AGENT_BACKSTOP_S,
    DEFAULT_AGENT_IDLE_TIMEOUT_S,
    DEFAULT_MAX_TURNS,
    MODELS,
)
from agentic_dev.exceptions import AgentRunError, RateLimitError
from agentic_dev.logging import get_event_logger, emit
from agentic_dev.logging.context import get_run_context
from agentic_dev.logging.events import (
    AgentStartEvent,
    AgentCompleteEvent,
    AgentFailedEvent,
    AgentRetryEvent,
)

_event_log = get_event_logger("runner")

# How often the output collector wakes to check whether the CLI has exited.
_OUTPUT_POLL_INTERVAL_S = 5.0
# After the CLI exits, how long to wait for its pipes to reach EOF once the
# process group has been reaped, before giving up on stdout.
_DRAIN_GRACE_S = 10.0
# Grace between SIGTERM and SIGKILL when reaping a process group.
_PROCESS_GROUP_TERM_GRACE_S = 5.0


async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    """Reap the CLI's process group — the CLI plus anything it left running.

    The subprocess is started with ``start_new_session=True``, so the CLI leads
    its own process group whose id equals its pid. That pgid stays valid for
    ``killpg`` even *after* the leader itself exits, as long as a child (e.g. a
    dev server the agent backgrounded) survives in the group — which is exactly
    the case we need to clean up. Children that re-``setsid`` into a brand-new
    session detach and cannot be reached this way; those rely on the agent's own
    teardown step.

    Sends ``SIGTERM``, polls until the group is empty (or a short grace
    elapses), then ``SIGKILL`` any survivors. ``killpg`` on an already-empty
    group raises ``ProcessLookupError`` and is treated as "nothing to do".
    """
    pgid = process.pid
    # Never signal pgid 0 (the caller's own group) or 1 (init): a missing or
    # bogus pid must not take down the orchestrator — or the machine.
    if not isinstance(pgid, int) or pgid <= 1:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return

    waited = 0.0
    while waited < _PROCESS_GROUP_TERM_GRACE_S:
        await asyncio.sleep(0.2)
        waited += 0.2
        try:
            os.killpg(pgid, 0)  # probe: does the group still have members?
        except (ProcessLookupError, PermissionError):
            return  # group is gone

    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


async def _cancel_task(task: "asyncio.Future") -> None:
    """Cancel a task and swallow the resulting CancelledError."""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@dataclass(frozen=True)
class StallInfo:
    """Why a wedged CLI was killed, plus a live snapshot of its process group.

    ``kind`` records the *origin* of the wedge: ``"idle"`` (alive but its
    transcript stopped advancing — a candidate for a resume-retry when the
    stall is a model stall) or ``"backstop"`` (the CLI process never exited at
    all — never retried).
    """

    reason: str
    proc_snapshot: str
    kind: str = "idle"


def _snapshot_process_group(pgid: int) -> str:
    """Best-effort table of the live processes in ``pgid`` (Linux ``/proc``).

    Captured *before* the group is reaped so a wedge report shows the CLI plus
    the dev servers / MCP / browser it spawned, and what each is blocked on
    (``wchan``). Never raises — diagnostics must not get in the way of the kill.
    """
    try:
        rows: list[str] = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text()
                rbrace = stat.rindex(")")
                fields = stat[rbrace + 2:].split()
                state = fields[0]
                pgrp = int(fields[2])
            except (OSError, ValueError, IndexError):
                continue
            if pgrp != pgid:
                continue
            try:
                wchan = (entry / "wchan").read_text().strip() or "-"
            except OSError:
                wchan = "?"
            try:
                cmdline = (
                    (entry / "cmdline").read_text().replace("\x00", " ").strip()
                )
            except OSError:
                cmdline = ""
            rows.append(f"  {entry.name:>7} {state} {wchan:<22} {cmdline[:100]}")
        if not rows:
            return f"process group {pgid}: no live members"
        return "\n".join(
            [f"process group {pgid} ({len(rows)} live) — pid state wchan cmd", *rows]
        )
    except Exception as exc:  # noqa: BLE001 — diagnostics are best-effort
        return f"process snapshot unavailable: {exc}"


def _latest_session_activity(
    working_dir: Path, claude_dir: Path | None = None
) -> float | None:
    """Newest session-transcript mtime for ``working_dir`` (progress heartbeat).

    The Claude CLI appends to ``~/.claude/projects/<encoded>/<session>.jsonl`` as
    the agent works, so the newest ``*.jsonl`` mtime is a reliable "is it making
    progress" signal. Returns ``None`` when no transcript exists yet. Unambiguous
    because the orchestrator runs agents sequentially — only one CLI writes
    transcripts for a given working dir at a time.
    """
    if claude_dir is None:
        claude_dir = Path.home() / ".claude"
    encoded_path = str(working_dir).replace("/", "-")
    sessions_dir = claude_dir / "projects" / encoded_path
    if not sessions_dir.is_dir():
        return None
    newest: float | None = None
    for path in sessions_dir.glob("*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if newest is None or mtime > newest:
            newest = mtime
    return newest


async def _collect_output(
    process: asyncio.subprocess.Process,
    prompt: str,
    working_dir: Path,
    backstop_s: float,
    idle_timeout_s: float,
) -> tuple[bytes, bytes, StallInfo | None]:
    """Collect ``(stdout, stderr)`` keyed on the CLI's *process exit* + progress.

    ``process.communicate`` blocks until the stdout/stderr pipes reach EOF — not
    until the CLI exits. A dev server the agent backgrounds inherits those pipes
    and holds them open, so ``communicate`` alone can hang even after the CLI has
    finished. And a CLI can wedge while *still alive* (e.g. a stalled upstream
    model stream), making no progress yet never exiting. So we drive
    ``communicate`` as a task and watch both ``process.returncode`` and the
    session-transcript heartbeat:

    - communicate completes (EOF) → the CLI exited and nothing is holding the
      pipe open; return its output directly.
    - the CLI has exited but communicate is still pending → a child holds the
      pipe; reap the process group to force EOF, then return the real buffered
      output. If a fully-detached child still holds it past a short grace,
      abandon stdout (caller recovers from the session transcript).
    - the CLI is still running but its transcript has not advanced for
      ``idle_timeout_s`` → wedged; reap and report it.
    - the CLI never exits within ``backstop_s`` → absolute-ceiling wedge; reap
      and report it.

    The third element is ``None`` on success, or a ``StallInfo`` (reason + a live
    process-group snapshot taken before the reap) when the run was killed as
    wedged (the caller diagnoses it and raises ``AgentRunError``).
    ``process.returncode`` is set by asyncio's child watcher on exit,
    independently of communicate's blocked read. Safe to poll because the
    orchestrator runs agents sequentially (one CLI process at a time).
    """
    comm_task = asyncio.ensure_future(
        process.communicate(input=prompt.encode("utf-8"))
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + backstop_s
    last_progress = loop.time()
    last_activity = _latest_session_activity(working_dir)

    while True:
        done, _ = await asyncio.wait(
            {comm_task}, timeout=_OUTPUT_POLL_INTERVAL_S
        )
        if comm_task in done:
            # EOF reached: the CLI exited and no spawned process is holding the
            # pipe open, so there is nothing to reap here. (A server the agent
            # backgrounds with its output redirected away is torn down by the
            # agent itself — see the UAT "Server lifecycle" rule.)
            stdout_bytes, stderr_bytes = comm_task.result()
            return stdout_bytes, stderr_bytes, None

        if process.returncode is not None:
            # CLI exited but a spawned process holds the pipe open. Reap the
            # group to close the inherited fds, then drain the buffered output.
            await _terminate_process_group(process)
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    asyncio.shield(comm_task), timeout=_DRAIN_GRACE_S
                )
                return stdout_bytes, stderr_bytes, None
            except asyncio.TimeoutError:
                # Fully-detached child still holds the pipe; give up on stdout.
                await _cancel_task(comm_task)
                return b"", b"", None

        # CLI still running: check the transcript heartbeat for a live wedge.
        current_activity = _latest_session_activity(working_dir)
        if current_activity is not None and (
            last_activity is None or current_activity > last_activity
        ):
            last_activity = current_activity
            last_progress = loop.time()

        if loop.time() - last_progress > idle_timeout_s:
            # Alive but no transcript progress for too long — a wedged CLI.
            # Snapshot the live process tree *before* reaping it.
            snapshot = _snapshot_process_group(process.pid)
            await _terminate_process_group(process)
            await _cancel_task(comm_task)
            return b"", b"", StallInfo(
                reason=f"made no progress for {idle_timeout_s:.0f}s (wedged)",
                proc_snapshot=snapshot,
                kind="idle",
            )

        if loop.time() >= deadline:
            # The CLI process itself never exited: absolute-ceiling wedge.
            snapshot = _snapshot_process_group(process.pid)
            await _terminate_process_group(process)
            await _cancel_task(comm_task)
            return b"", b"", StallInfo(
                reason=f"did not exit within {backstop_s:.0f}s (wedged)",
                proc_snapshot=snapshot,
                kind="backstop",
            )


@runtime_checkable
class AgentConfig(Protocol):
    """Lightweight protocol describing the agent fields needed by the runner.

    The real AgentDefinition (in agents.base) will satisfy this interface.
    """

    name: str
    model: str
    permission_mode: str
    allowed_tools: list[str]
    max_turns: int
    use_bare_mode: bool
    mcp_config: Path | None
    system_prompt: str | None
    timeout_s: int | None
    idle_timeout_s: int | None


@dataclass(frozen=True)
class ClaudeResult:
    """Structured result from a Claude CLI invocation."""

    text: str
    session_id: str | None
    cost_usd: float
    exit_code: int
    raw_json: dict[str, object] = field(default_factory=dict)


class ClaudeRunner:
    """Async wrapper that builds and executes Claude CLI commands.

    Prompts are piped via stdin (``claude -p - ...``) rather than passed as
    CLI arguments.  This avoids OS-level ``ARG_MAX`` limits and shell-escaping
    issues with long or special-character-heavy prompts.

    Rate-limited invocations are retried with a layered wait strategy:
    1. Parse explicit wait time from stderr
    2. Poll the Anthropic usage API
    3. Exponential backoff as fallback

    When a failed run produces a ``session_id``, subsequent retries use
    ``--resume`` to continue the session rather than starting fresh.
    """

    def __init__(
        self,
        log_dir: Path | None = None,
        max_retries: int = 5,
        base_delay: float = 30.0,
        enable_usage_api: bool = True,
        usage_client: UsageApiClient | None = None,
        max_stall_retries: int = 2,
    ) -> None:
        self._log_dir = log_dir
        self._max_retries = max_retries
        # Dedicated, small budget for resuming through transient *model stalls*.
        # Kept separate from ``max_retries`` because each stall re-detection costs
        # a full idle window, so the wall-clock bleed must be bounded tightly.
        self._max_stall_retries = max_stall_retries
        if usage_client is None and enable_usage_api:
            usage_client = UsageApiClient()
        self._usage_client = usage_client
        self._wait_strategy = WaitStrategy(
            usage_client=usage_client, base_delay=base_delay,
        )

    async def _usage_api_indicates_limit(self) -> bool:
        """Ask the Anthropic usage API whether we are currently rate-limited.

        Used as a fallback when the CLI exits non-zero with a stderr that
        doesn't match any known rate-limit pattern (observed in the wild
        when the CLI exits silently during a 5-hour quota window).

        Returns ``False`` when the usage client is not configured or the
        request fails — we prefer to surface a clean AgentRunError over
        looping forever on an unknown failure.
        """
        if self._usage_client is None:
            return False
        try:
            status = await self._usage_client.get_utilization()
        except Exception:  # noqa: BLE001 — degrade gracefully
            return False
        return status is not None and status.is_limited

    def _resolve_model(self, model_alias: str) -> str:
        """Resolve a short model alias (e.g. 'opus') to a full model ID."""
        if model_alias in MODELS:
            return MODELS[model_alias]
        return model_alias

    def build_command(
        self,
        agent: AgentConfig,
        working_dir: Path,
        session_id: str | None = None,
        extra_add_dirs: list[Path] | None = None,
    ) -> list[str]:
        """Build the CLI argument list for a Claude invocation.

        The prompt is NOT included in the command — it is piped via stdin
        using ``-p -`` (read prompt from stdin).
        """
        model = self._resolve_model(agent.model)
        max_turns = agent.max_turns or DEFAULT_MAX_TURNS

        cmd: list[str] = ["claude", "-p", "-"]

        cmd.extend(
            [
                "--output-format",
                "json",
                "--model",
                model,
                "--permission-mode",
                agent.permission_mode,
                "--max-turns",
                str(max_turns),
            ]
        )

        # Always pass --allowedTools so Claude doesn't default to all tools.
        # An empty list means "no tools" — pass "" to explicitly disable tool use.
        allowed_tools_value = ",".join(agent.allowed_tools) if agent.allowed_tools else ""
        cmd.extend(["--allowedTools", allowed_tools_value])

        if session_id:
            cmd.extend(["--resume", session_id])

        if agent.mcp_config:
            cmd.extend(["--mcp-config", str(agent.mcp_config)])

        if extra_add_dirs:
            for add_dir in extra_add_dirs:
                cmd.extend(["--add-dir", str(add_dir)])

        if agent.system_prompt:
            cmd.extend(["--system-prompt", agent.system_prompt])

        return cmd

    @staticmethod
    def _recover_result_from_session(
        session_id: str,
        working_dir: Path,
        claude_dir: Path | None = None,
    ) -> str:
        """Attempt to extract the last assistant text from a session JSONL.

        This is a fallback for when the Claude CLI returns an empty ``result``
        field despite the agent having produced text.  The session JSONL is
        stored by the CLI at ``~/.claude/projects/{encoded_path}/{session_id}.jsonl``.

        Returns the recovered text, or an empty string if recovery fails.
        """
        if claude_dir is None:
            claude_dir = Path.home() / ".claude"

        encoded_path = str(working_dir).replace("/", "-")
        jsonl_path = claude_dir / "projects" / encoded_path / f"{session_id}.jsonl"

        if not jsonl_path.exists():
            return ""

        try:
            lines = jsonl_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ""

        # Scan backwards for the last assistant message with text content
        for line in reversed(lines):
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("type") != "assistant":
                continue
            content = msg.get("message", {}).get("content", [])
            text_parts = [
                block["text"]
                for block in content
                if block.get("type") == "text" and block.get("text")
            ]
            if text_parts:
                return "".join(text_parts)

        return ""

    @staticmethod
    def _recover_longest_from_session(
        session_id: str,
        working_dir: Path,
        claude_dir: Path | None = None,
    ) -> str:
        """Find the longest assistant text from a session JSONL.

        Unlike ``_recover_result_from_session`` (which returns the *last*
        assistant message), this method scans *all* assistant messages and
        returns the one with the most text.  This is useful when the CLI
        ``result`` field captured a short trailing summary while the real
        document lives in an earlier message.

        Returns the longest text, or an empty string if recovery fails.
        """
        if claude_dir is None:
            claude_dir = Path.home() / ".claude"

        encoded_path = str(working_dir).replace("/", "-")
        jsonl_path = claude_dir / "projects" / encoded_path / f"{session_id}.jsonl"

        if not jsonl_path.exists():
            return ""

        try:
            lines = jsonl_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ""

        longest = ""
        for line in lines:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("type") != "assistant":
                continue
            content = msg.get("message", {}).get("content", [])
            text_parts = [
                block["text"]
                for block in content
                if block.get("type") == "text" and block.get("text")
            ]
            if text_parts:
                combined = "".join(text_parts)
                if len(combined) > len(longest):
                    longest = combined

        return longest

    @staticmethod
    def _discover_session_id(
        working_dir: Path,
        since: datetime,
        claude_dir: Path | None = None,
    ) -> str | None:
        """Return the id of the newest session transcript for ``working_dir``.

        Used to salvage a result when the CLI's stdout could not be read (a
        detached child held the pipe). Picks the most recently modified
        ``*.jsonl`` written at/after ``since``. Unambiguous because the
        orchestrator runs agents sequentially — only one CLI writes transcripts
        for a given working dir at a time.
        """
        if claude_dir is None:
            claude_dir = Path.home() / ".claude"
        encoded_path = str(working_dir).replace("/", "-")
        sessions_dir = claude_dir / "projects" / encoded_path
        if not sessions_dir.is_dir():
            return None

        cutoff = since.timestamp() - 5.0  # small slack for clock granularity
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

    @staticmethod
    def _diagnose_stall(
        working_dir: Path,
        session_id: str | None,
        start_time: datetime,
        claude_dir: Path | None = None,
    ) -> tuple[str, str, str]:
        """Classify a stall from the session-transcript tail.

        Returns ``(kind, summary, tail_excerpt)`` where ``kind`` is one of
        ``"model_stall"`` (a trailing ``tool_result`` / user turn awaiting the
        next model turn — the transient skillsbloom uat_web case, safe to resume),
        ``"tool_hang"`` (a trailing ``tool_use`` with no result — stuck inside a
        tool), ``"unknown"``, or ``"unavailable"``. ``summary`` is the
        human-readable form of the same. Best-effort: any failure degrades to an
        ``"unavailable"`` classification rather than raising.
        """
        try:
            if claude_dir is None:
                claude_dir = Path.home() / ".claude"
            sid = session_id or ClaudeRunner._discover_session_id(
                working_dir, start_time, claude_dir
            )
            if not sid:
                return (
                    "unavailable",
                    "transcript diagnosis unavailable (no session id)",
                    "",
                )
            encoded = str(working_dir).replace("/", "-")
            path = claude_dir / "projects" / encoded / f"{sid}.jsonl"
            if not path.exists():
                return (
                    "unavailable",
                    "transcript diagnosis unavailable (no transcript)",
                    "",
                )

            last_tool: str | None = None
            last_ts = ""
            pending_tool: str | None = None
            excerpt: list[str] = []
            for raw in path.read_text(encoding="utf-8").splitlines()[-30:]:
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                ts = msg.get("timestamp", "") or ""
                kinds: list[str] = []
                for block in iter_content_blocks(msg):
                    bt = block.get("type")
                    if bt == "tool_use":
                        last_tool = block.get("name") or last_tool
                        pending_tool = block.get("name")
                        kinds.append(f"tool_use:{block.get('name')}")
                    elif bt == "tool_result":
                        pending_tool = None  # the tool returned
                        kinds.append("tool_result")
                    elif bt == "text":
                        pending_tool = None
                        kinds.append("text")
                if ts:
                    last_ts = ts
                if kinds:
                    excerpt.append(f"[{ts}] {msg.get('type')}: {', '.join(kinds)}")

            if pending_tool:
                kind = "tool_hang"
                summary = (
                    f"tool hang: stuck in tool '{pending_tool}' "
                    f"(no result; last activity {last_ts})"
                )
            elif last_tool:
                kind = "model_stall"
                summary = (
                    f"model stall: awaiting model response after tool "
                    f"'{last_tool}' (last activity {last_ts})"
                )
            else:
                kind = "unknown"
                summary = f"unknown stall (last activity {last_ts})"
            return (kind, summary, "\n".join(excerpt[-12:]))
        except Exception as exc:  # noqa: BLE001 — best-effort
            return ("unavailable", f"transcript diagnosis unavailable: {exc}", "")

    @staticmethod
    def _extract_session_id(stdout: str) -> str | None:
        """Try to extract session_id from potentially partial JSON output."""
        try:
            data = json.loads(stdout)
            result: str | None = data.get("session_id")
            return result
        except (json.JSONDecodeError, ValueError):
            return None

    @staticmethod
    def _session_has_api_error(
        session_id: str | None,
        working_dir: Path,
        claude_dir: Path | None = None,
    ) -> bool:
        """Detect transient Anthropic API errors (5xx) in the session JSONL.

        When the Claude CLI hits an upstream API error mid-session, it exits
        non-zero with an empty stderr; the error text is only persisted in the
        session JSONL as a synthetic assistant message with
        ``isApiErrorMessage: true`` and ``type: api_error``. Use this as the
        signal to retry rather than failing the agent run.
        """
        if not session_id:
            return False
        if claude_dir is None:
            claude_dir = Path.home() / ".claude"
        encoded_path = str(working_dir).replace("/", "-")
        jsonl_path = claude_dir / "projects" / encoded_path / f"{session_id}.jsonl"
        if not jsonl_path.exists():
            return False
        try:
            lines = jsonl_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False
        # Scan only the tail — API errors land at the end of the session.
        for line in reversed(lines[-20:]):
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if entry.get("isApiErrorMessage"):
                return True
            content = entry.get("message", {}).get("content")
            if isinstance(content, list):
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "text"
                        and "api_error" in (block.get("text") or "")
                    ):
                        return True
        return False

    async def run(
        self,
        agent: AgentConfig,
        prompt: str,
        working_dir: Path,
        session_id: str | None = None,
        extra_add_dirs: list[Path] | None = None,
    ) -> ClaudeResult:
        """Invoke the Claude CLI and return the parsed result.

        The prompt is piped to the process via stdin.  Rate-limited
        invocations are retried automatically using a layered wait
        strategy.  When a failed run yields a ``session_id``,
        subsequent retries use ``--resume`` to continue the session.

        Raises:
            AgentRunError: If the CLI exits with a non-rate-limit error.
            RateLimitError: If all rate-limit retries are exhausted.
        """
        cmd = self.build_command(
            agent=agent,
            working_dir=working_dir,
            session_id=session_id,
            extra_add_dirs=extra_add_dirs,
        )

        ctx = get_run_context()
        sprint = ctx.sprint_number if ctx else None
        start_time = datetime.now(timezone.utc)
        backstop_s = getattr(agent, "timeout_s", None) or DEFAULT_AGENT_BACKSTOP_S
        idle_timeout_s = (
            getattr(agent, "idle_timeout_s", None) or DEFAULT_AGENT_IDLE_TIMEOUT_S
        )

        emit(_event_log, AgentStartEvent(
            agent_name=agent.name,
            model=agent.model,
            prompt_length=len(prompt),
            working_dir=str(working_dir),
            sprint=sprint,
            message=f"Running agent '{agent.name}' (model={agent.model}) in {working_dir} [prompt={len(prompt)} chars]",
        ))

        resume_session_id = session_id
        exit_code = 0
        stdout_text = ""
        model_stall_retries = 0

        for attempt in range(self._max_retries + 1):
            if attempt > 0 and resume_session_id:
                cmd = self.build_command(
                    agent=agent,
                    working_dir=working_dir,
                    session_id=resume_session_id,
                    extra_add_dirs=extra_add_dirs,
                )

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(working_dir),
                # Lead a new process group so we can reap the whole tree (the CLI
                # plus any dev servers the agent spawns) once the CLI exits.
                start_new_session=True,
            )

            # Surface what the agent is doing by following its live transcript.
            # Read-only and strictly scoped to this subprocess: always cancelled
            # before the wedge/exit handling below, so it never interferes with
            # process reaping or stall diagnosis.
            activity_tailer = asyncio.create_task(tail_transcript_activity(
                working_dir,
                start_time,
                resume_session_id,
                agent.name,
                sprint,
                logger=_event_log,
            ))
            try:
                stdout_bytes, stderr_bytes, wedged = await _collect_output(
                    process, prompt, working_dir, backstop_s, idle_timeout_s
                )
            finally:
                activity_tailer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await activity_tailer
            if wedged is not None:
                # The CLI hung — either no transcript progress (alive but wedged)
                # or it never exited within the backstop. The group was already
                # reaped; diagnose what it was stuck on and persist a report.
                # Diagnostics are best-effort and must never mask the failure, so a
                # diagnosis error degrades to a non-retryable "unavailable".
                try:
                    diag_kind, summary, tail = self._diagnose_stall(
                        working_dir, resume_session_id, start_time
                    )
                    report = self._save_stall_report(agent, wedged, summary, tail)
                except Exception:  # noqa: BLE001 — never let diagnostics break the raise
                    diag_kind, summary, report = (
                        "unavailable", "diagnosis unavailable", None,
                    )

                # A *model stall* — the CLI is alive but blocked awaiting the
                # model's next response (an upstream hiccup; the process snapshot
                # shows it idle in ep_poll with no hung child) — is transient,
                # exactly like the API-timeout case handled further below. Resume
                # the session and retry rather than failing the whole pipeline.
                # Gated tightly: only the idle-timeout wedge (not the wall-clock
                # backstop), only a model stall (not a tool hang — a likely
                # deterministic block that resuming would just re-enter), and only
                # within a small dedicated budget, since each re-detection costs a
                # full idle window.
                if (
                    wedged.kind == "idle"
                    and diag_kind == "model_stall"
                    and model_stall_retries < self._max_stall_retries
                    and attempt < self._max_retries
                ):
                    resume_target = resume_session_id or self._discover_session_id(
                        working_dir, start_time
                    )
                    if resume_target:
                        resume_session_id = resume_target
                        model_stall_retries += 1
                        wait_seconds = 30.0 * (attempt + 1)
                        emit(_event_log, AgentRetryEvent(
                            agent_name=agent.name,
                            model=agent.model,
                            attempt=attempt + 1,
                            max_retries=self._max_retries,
                            wait_seconds=wait_seconds,
                            wait_source="model_stall_backoff",
                            reason="model_stall",
                            will_resume_session=True,
                            sprint=sprint,
                            message=(
                                f"Agent '{agent.name}' stalled awaiting model "
                                f"response; resuming session {resume_target} in "
                                f"{wait_seconds:.0f}s (stall retry "
                                f"{model_stall_retries}/{self._max_stall_retries})"
                            ),
                        ))
                        await asyncio.sleep(wait_seconds)
                        continue

                duration_s = (datetime.now(timezone.utc) - start_time).total_seconds()
                detail = f"{wedged.reason} — {summary}"
                emit(_event_log, AgentFailedEvent(
                    agent_name=agent.name,
                    model=agent.model,
                    duration_s=duration_s,
                    exit_code=-1,
                    error=detail,
                    sprint=sprint,
                    level="ERROR",
                    message=(
                        f"Agent '{agent.name}' {detail}"
                        + (f" — report: {report}" if report else "")
                    ),
                ))
                raise AgentRunError(
                    agent_name=agent.name,
                    message=f"Agent '{agent.name}' {detail}",
                    exit_code=-1,
                    # Carry the wedged session so a later `agentic-dev resume`
                    # continues it rather than re-running the agent from scratch.
                    session_id=(
                        resume_session_id
                        or self._discover_session_id(working_dir, start_time)
                    ),
                )
            exit_code = process.returncode or 0
            stdout_text = stdout_bytes.decode("utf-8", errors="replace")

            if exit_code == 0:
                break

            stderr_text = stderr_bytes.decode("utf-8", errors="replace")

            # Try to extract session_id for resume on retry
            extracted_sid = self._extract_session_id(stdout_text)
            if extracted_sid:
                resume_session_id = extracted_sid

            # A hard API timeout exits the CLI 1 with empty stdout — no session_id
            # to extract. Locate the just-written transcript so the api-error
            # check can still fire and the retry can resume the session instead
            # of failing the whole pipeline.
            if resume_session_id is None:
                resume_session_id = self._discover_session_id(
                    working_dir, start_time
                )

            # Retry rate limits AND transient upstream API errors. Both surface
            # as exit-1 with empty stderr; the API-error variant is only
            # visible in the session JSONL as an ``isApiErrorMessage`` entry.
            rate_limit_detected = RateLimitDetector.is_rate_limit(stderr_text)
            rate_limit_from_usage_api = False
            if not rate_limit_detected:
                rate_limit_from_usage_api = await self._usage_api_indicates_limit()

            api_error_detected = False
            if not rate_limit_detected and not rate_limit_from_usage_api:
                api_error_detected = self._session_has_api_error(
                    resume_session_id, working_dir,
                )

            if (
                not rate_limit_detected
                and not rate_limit_from_usage_api
                and not api_error_detected
            ):
                duration_s = (datetime.now(timezone.utc) - start_time).total_seconds()
                emit(_event_log, AgentFailedEvent(
                    agent_name=agent.name,
                    model=agent.model,
                    duration_s=duration_s,
                    exit_code=exit_code,
                    error=stderr_text[:500],
                    sprint=sprint,
                    level="ERROR",
                    message=f"Agent '{agent.name}' failed (exit={exit_code}): {stderr_text[:200]}",
                ))
                raise AgentRunError(
                    agent_name=agent.name,
                    message=f"CLI exited with code {exit_code}: {stderr_text}",
                    exit_code=exit_code,
                    session_id=resume_session_id,
                )

            # Exhausted retries — raise immediately
            if attempt >= self._max_retries:
                duration_s = (datetime.now(timezone.utc) - start_time).total_seconds()
                exhaustion_label = (
                    "transient API errors" if api_error_detected else "rate limit"
                )
                emit(_event_log, AgentFailedEvent(
                    agent_name=agent.name,
                    model=agent.model,
                    duration_s=duration_s,
                    exit_code=exit_code,
                    error=stderr_text[:500],
                    sprint=sprint,
                    level="ERROR",
                    message=(
                        f"Agent '{agent.name}' failed after {self._max_retries + 1} "
                        f"attempts ({exhaustion_label})"
                    ),
                ))
                if api_error_detected:
                    raise AgentRunError(
                        agent_name=agent.name,
                        message=(
                            f"Transient API errors after {self._max_retries + 1} "
                            f"attempts; last exit code {exit_code}"
                        ),
                        exit_code=exit_code,
                        session_id=resume_session_id,
                    )
                raise RateLimitError(
                    agent_name=agent.name,
                    message=f"Rate limited after {self._max_retries + 1} attempts: {stderr_text}",
                    attempts=self._max_retries + 1,
                    exit_code=exit_code,
                    session_id=resume_session_id,
                )

            if api_error_detected:
                # Linear backoff for transient API errors; the upstream
                # incident usually clears within a minute.
                wait_seconds = 30.0 * (attempt + 1)
                wait_source = "api_error_backoff"
                retry_reason = "upstream_api_error"
            else:
                wait_result = await self._wait_strategy.determine_wait(
                    stderr_text, attempt, return_source=True,
                )
                assert isinstance(wait_result, tuple)
                wait_seconds, wait_source = wait_result

                retry_reason = stderr_text[:200]
                if rate_limit_from_usage_api and not stderr_text.strip():
                    retry_reason = "empty_stderr_usage_api_limited"

            emit(_event_log, AgentRetryEvent(
                agent_name=agent.name,
                model=agent.model,
                attempt=attempt + 1,
                max_retries=self._max_retries,
                wait_seconds=wait_seconds,
                wait_source=wait_source,
                reason=retry_reason,
                will_resume_session=resume_session_id is not None,
                sprint=sprint,
                message=(
                    f"Agent '{agent.name}' retrying ({retry_reason}), "
                    f"waiting {wait_seconds:.0f}s "
                    f"(attempt {attempt + 1}/{self._max_retries}, source={wait_source})"
                    + (f" [resuming session {resume_session_id}]" if resume_session_id else "")
                ),
            ))

            await asyncio.sleep(wait_seconds)

        # --- Success path ---

        try:
            raw_json = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            # The CLI exited but its stdout JSON could not be read — a
            # fully-detached child the agent spawned is still holding the pipe.
            # Salvage the output from the session transcript rather than
            # discarding completed work.
            recovery_sid = resume_session_id or self._discover_session_id(
                working_dir, start_time
            )
            recovered = ""
            if exit_code == 0 and recovery_sid:
                recovered = self._recover_longest_from_session(
                    recovery_sid, working_dir
                ) or self._recover_result_from_session(recovery_sid, working_dir)
            if not recovered.strip():
                raise AgentRunError(
                    agent_name=agent.name,
                    message=f"Failed to parse JSON output: {exc}",
                ) from exc
            result = ClaudeResult(
                text=recovered,
                session_id=recovery_sid,
                cost_usd=0.0,
                exit_code=exit_code,
                raw_json={},
            )
            duration_s = (datetime.now(timezone.utc) - start_time).total_seconds()
            emit(_event_log, AgentCompleteEvent(
                agent_name=agent.name,
                model=agent.model,
                duration_s=duration_s,
                cost_usd=0.0,
                result_length=len(result.text),
                session_id=result.session_id,
                sprint=sprint,
                level="WARNING",
                message=(
                    f"Agent '{agent.name}' output recovered from transcript "
                    f"(stdout pipe held open by a detached child)"
                ),
            ))
            self._save_log(agent, prompt, result)
            return result

        result_text = raw_json.get("result", "") or ""
        sid = raw_json.get("session_id")

        # Fallback recovery from session JSONL when the CLI result is
        # missing or appears to be a trailing summary.
        #
        # Both the "empty" and "short" cases can occur when the agent produced
        # a long document and then signed off with a brief status line — the
        # final CLI ``result`` field captures only the sign-off.  In both
        # cases we prefer a substantially longer earlier assistant message
        # when one exists.  If not, and the result is empty, fall back to the
        # last assistant text (which at least gives us something rather than
        # an empty string).
        if sid and (not result_text.strip() or len(result_text) < 500):
            longest = self._recover_longest_from_session(sid, working_dir)
            threshold = max(len(result_text) * 5, 1000)
            if longest.strip() and len(longest) > threshold:
                result_text = longest
            elif not result_text.strip():
                recovered = self._recover_result_from_session(sid, working_dir)
                if recovered.strip():
                    result_text = recovered

        result = ClaudeResult(
            text=result_text,
            session_id=sid,
            cost_usd=float(raw_json.get("total_cost_usd", 0.0)),
            exit_code=exit_code,
            raw_json=raw_json,
        )

        duration_s = (datetime.now(timezone.utc) - start_time).total_seconds()
        level = "WARNING" if not result.text.strip() else "INFO"
        msg = (
            f"Agent '{agent.name}' returned empty result text"
            if not result.text.strip()
            else f"Agent '{agent.name}' complete ({duration_s:.1f}s, ${result.cost_usd:.4f})"
        )
        emit(_event_log, AgentCompleteEvent(
            agent_name=agent.name,
            model=agent.model,
            duration_s=duration_s,
            cost_usd=result.cost_usd,
            result_length=len(result.text),
            session_id=result.session_id,
            sprint=sprint,
            level=level,
            message=msg,
        ))

        self._save_log(agent, prompt, result)

        return result

    def _save_stall_report(
        self,
        agent: AgentConfig,
        stall: StallInfo,
        summary: str,
        tail: str,
    ) -> Path | None:
        """Write a wedge report under ``log_dir/stalls/``. Best-effort.

        Returns the report path, or ``None`` if there is no log dir or writing
        fails — diagnostics must never break the failure path.
        """
        if self._log_dir is None:
            return None
        try:
            stalls_dir = self._log_dir / "stalls"
            stalls_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = stalls_dir / f"{agent.name}_{ts}.md"
            path.write_text(
                f"# Stall report: {agent.name}\n\n"
                f"- When: {ts}\n"
                f"- Reason: {stall.reason}\n"
                f"- Diagnosis: {summary}\n\n"
                f"## Transcript tail\n\n```\n{tail or '(unavailable)'}\n```\n\n"
                f"## Process group snapshot (before reap)\n\n"
                f"```\n{stall.proc_snapshot}\n```\n",
                encoding="utf-8",
            )
            return path
        except OSError:
            return None

    def _save_log(
        self, agent: AgentConfig, prompt: str, result: ClaudeResult
    ) -> None:
        """Persist a JSON log entry for this agent run."""
        if self._log_dir is None:
            return

        dump_dir = self._log_dir / "agent_dumps"
        dump_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_file = dump_dir / f"{agent.name}_{timestamp}.json"

        entry = {
            "agent_name": agent.name,
            "timestamp": timestamp,
            "model": agent.model,
            "prompt_length": len(prompt),
            "prompt": prompt,
            "exit_code": result.exit_code,
            "result_length": len(result.text),
            "cost_usd": result.cost_usd,
            "session_id": result.session_id,
            "raw_json": result.raw_json,
        }
        try:
            log_file.write_text(
                json.dumps(entry, indent=2, default=str), encoding="utf-8"
            )
        except OSError:
            pass
