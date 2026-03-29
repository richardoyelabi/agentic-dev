"""Async subprocess wrapper for invoking Claude Code CLI."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from agentic_dev.config import DEFAULT_MAX_TURNS, MODELS
from agentic_dev.exceptions import AgentRunError

logger = logging.getLogger(__name__)


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

        logger.info(
            "Running agent '%s' (model=%s) in %s [prompt=%d chars]",
            agent.name,
            agent.model,
            working_dir,
            len(prompt),
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

        if exit_code != 0:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
            logger.error(
                "Agent '%s' failed (exit=%d): %s",
                agent.name,
                exit_code,
                stderr_text[:500],
            )
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

        if not result.text.strip():
            logger.warning("Agent '%s' returned empty result text", agent.name)

        self._save_log(agent, prompt, result)

        return result

    def _save_log(
        self, agent: AgentConfig, prompt: str, result: ClaudeResult
    ) -> None:
        """Persist a JSON log entry for this agent run."""
        if self._log_dir is None:
            return

        from datetime import datetime, timezone

        self._log_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_file = self._log_dir / f"{agent.name}_{timestamp}.json"

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
            logger.warning("Failed to write log to %s", log_file)
