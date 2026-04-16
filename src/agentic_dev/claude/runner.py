"""Async subprocess wrapper for invoking Claude Code CLI."""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from agentic_dev.claude.rate_limiter import (
    RateLimitDetector,
    UsageApiClient,
    WaitStrategy,
)
from agentic_dev.config import DEFAULT_MAX_TURNS, MODELS
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
    ) -> None:
        self._log_dir = log_dir
        self._max_retries = max_retries
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
    def _extract_session_id(stdout: str) -> str | None:
        """Try to extract session_id from potentially partial JSON output."""
        try:
            data = json.loads(stdout)
            result: str | None = data.get("session_id")
            return result
        except (json.JSONDecodeError, ValueError):
            return None

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
            )

            stdout_bytes, stderr_bytes = await process.communicate(
                input=prompt.encode("utf-8")
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

            # Only retry rate limits — other errors raise immediately.
            # Fallback: when stderr is empty or unrecognised, ask the usage
            # API before giving up — the CLI has been observed to exit
            # silently during 5-hour quota windows.
            rate_limit_detected = RateLimitDetector.is_rate_limit(stderr_text)
            rate_limit_from_usage_api = False
            if not rate_limit_detected:
                rate_limit_from_usage_api = await self._usage_api_indicates_limit()

            if not rate_limit_detected and not rate_limit_from_usage_api:
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
                )

            # Exhausted retries — raise immediately
            if attempt >= self._max_retries:
                duration_s = (datetime.now(timezone.utc) - start_time).total_seconds()
                emit(_event_log, AgentFailedEvent(
                    agent_name=agent.name,
                    model=agent.model,
                    duration_s=duration_s,
                    exit_code=exit_code,
                    error=stderr_text[:500],
                    sprint=sprint,
                    level="ERROR",
                    message=f"Agent '{agent.name}' rate limited after {self._max_retries + 1} attempts",
                ))
                raise RateLimitError(
                    agent_name=agent.name,
                    message=f"Rate limited after {self._max_retries + 1} attempts: {stderr_text}",
                    attempts=self._max_retries + 1,
                    exit_code=exit_code,
                )

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
                    f"Agent '{agent.name}' rate limited, waiting {wait_seconds:.0f}s "
                    f"(attempt {attempt + 1}/{self._max_retries}, source={wait_source})"
                    + (f" [resuming session {resume_session_id}]" if resume_session_id else "")
                ),
            ))

            await asyncio.sleep(wait_seconds)

        # --- Success path (unchanged) ---

        try:
            raw_json = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            raise AgentRunError(
                agent_name=agent.name,
                message=f"Failed to parse JSON output: {exc}",
            ) from exc

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
