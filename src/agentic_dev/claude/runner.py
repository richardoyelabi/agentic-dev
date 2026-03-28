"""Async subprocess wrapper for invoking Claude Code CLI."""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from agentic_dev.config import DEFAULT_MAX_TURNS, MODELS
from agentic_dev.exceptions import AgentRunError


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
    """Async wrapper that builds and executes Claude CLI commands."""

    def _resolve_model(self, model_alias: str) -> str:
        """Resolve a short model alias (e.g. 'opus') to a full model ID."""
        if model_alias in MODELS:
            return MODELS[model_alias]
        return model_alias

    def build_command(
        self,
        agent: AgentConfig,
        prompt: str,
        working_dir: Path,
        session_id: str | None = None,
        extra_add_dirs: list[Path] | None = None,
    ) -> list[str]:
        """Build the CLI argument list for a Claude invocation."""
        model = self._resolve_model(agent.model)
        max_turns = agent.max_turns or DEFAULT_MAX_TURNS

        cmd: list[str] = [
            "claude",
            "-p",
            "--output-format", "json",
            "--model", model,
            "--permission-mode", agent.permission_mode,
            "--max-turns", str(max_turns),
        ]

        if agent.allowed_tools:
            cmd.extend(["--allowedTools", ",".join(agent.allowed_tools)])

        if session_id:
            cmd.extend(["--resume", session_id])

        if agent.mcp_config:
            cmd.extend(["--mcp-config", str(agent.mcp_config)])

        if extra_add_dirs:
            for add_dir in extra_add_dirs:
                cmd.extend(["--add-dir", str(add_dir)])

        if agent.system_prompt:
            cmd.extend(["--system-prompt", agent.system_prompt])

        cmd.append(prompt)

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

        Raises:
            AgentRunError: If the CLI exits with a non-zero code or produces
                unparseable output.
        """
        cmd = self.build_command(
            agent=agent,
            prompt=prompt,
            working_dir=working_dir,
            session_id=session_id,
            extra_add_dirs=extra_add_dirs,
        )

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(working_dir),
        )

        stdout_bytes, stderr_bytes = await process.communicate()
        exit_code = process.returncode or 0
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")

        if exit_code != 0:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
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

        return ClaudeResult(
            text=raw_json.get("result", ""),
            session_id=raw_json.get("session_id"),
            cost_usd=float(raw_json.get("cost_usd", 0.0)),
            exit_code=exit_code,
            raw_json=raw_json,
        )
