"""Async subprocess wrapper for invoking Claude Code CLI."""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from agentic_dev.config import DEFAULT_MAX_TURNS, MODELS
from agentic_dev.exceptions import AgentRunError
from agentic_dev.logging import get_event_logger, emit
from agentic_dev.logging.context import get_run_context
from agentic_dev.logging.events import AgentStartEvent, AgentCompleteEvent, AgentFailedEvent

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
    raw_json: dict = field(default_factory=dict)


class ClaudeRunner:
    """Async wrapper that builds and executes Claude CLI commands.

    Prompts are piped via stdin (``claude -p - ...``) rather than passed as
    CLI arguments.  This avoids OS-level ``ARG_MAX`` limits and shell-escaping
    issues with long or special-character-heavy prompts.
    """

    def __init__(self, log_dir: Path | None = None) -> None:
        self._log_dir = log_dir

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

    async def run(
        self,
        agent: AgentConfig,
        prompt: str,
        working_dir: Path,
        session_id: str | None = None,
        extra_add_dirs: list[Path] | None = None,
    ) -> ClaudeResult:
        """Invoke the Claude CLI and return the parsed result.

        The prompt is piped to the process via stdin.

        Raises:
            AgentRunError: If the CLI exits with a non-zero code or produces
                unparseable output.
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

        if exit_code != 0:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
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

        try:
            raw_json = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            raise AgentRunError(
                agent_name=agent.name,
                message=f"Failed to parse JSON output: {exc}",
            ) from exc

        result = ClaudeResult(
            text=raw_json.get("result", ""),
            session_id=raw_json.get("session_id"),
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
