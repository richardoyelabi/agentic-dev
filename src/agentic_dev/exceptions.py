"""Custom exception hierarchy for the agentic-dev agency."""

from datetime import datetime


class AgenticDevError(Exception):
    """Base exception for all agentic-dev errors."""


class AgentRunError(AgenticDevError):
    """Raised when a Claude agent run fails."""

    def __init__(
        self,
        agent_name: str,
        message: str,
        exit_code: int | None = None,
        session_id: str | None = None,
    ):
        self.agent_name = agent_name
        self.exit_code = exit_code
        # The agent's Claude session id (when known), so the pipeline can
        # ``--resume`` it on the next ``agentic-dev resume`` instead of starting
        # the agent over.
        self.session_id = session_id
        super().__init__(f"Agent '{agent_name}' failed: {message}")


class OutputParseError(AgenticDevError):
    """Raised when agent output cannot be parsed."""

    def __init__(self, agent_name: str, message: str):
        self.agent_name = agent_name
        super().__init__(f"Failed to parse output from '{agent_name}': {message}")


class StateError(AgenticDevError):
    """Raised for invalid state transitions or corrupted state."""


class InvalidTransitionError(StateError):
    """Raised when an invalid pipeline phase transition is attempted."""

    def __init__(self, from_phase: str, to_phase: str):
        self.from_phase = from_phase
        self.to_phase = to_phase
        super().__init__(f"Invalid transition: {from_phase} -> {to_phase}")


class WorkspaceError(AgenticDevError):
    """Raised for workspace creation or management errors."""


class AgentDefinitionError(AgenticDevError):
    """Raised when an agent definition YAML is invalid."""

    def __init__(self, agent_name: str, message: str):
        self.agent_name = agent_name
        super().__init__(f"Invalid agent definition '{agent_name}': {message}")


class CheckpointPause(AgenticDevError):
    """Raised to signal the pipeline should pause at a checkpoint."""

    def __init__(self, phase: str, message: str = ""):
        self.phase = phase
        super().__init__(message or f"Pipeline paused at checkpoint: {phase}")


class GracefulShutdown(AgenticDevError):
    """Raised when a shutdown signal (SIGINT/SIGTERM) is received."""

    def __init__(self, phase: str, message: str = ""):
        self.phase = phase
        super().__init__(message or f"Graceful shutdown at phase: {phase}")


class RateLimitPause(AgenticDevError):
    """Signal that the pipeline should pause until the rate-limit window resets.

    This is a control-flow signal raised by the engine when a ``RateLimitError``
    bubbles up after the runner has exhausted its internal retries.  The CLI
    layer catches it, sleeps until the reset window, and re-enters
    ``engine.run()``.  It is NOT a failure — the pipeline state remains at the
    same phase throughout the pause.
    """

    def __init__(
        self,
        phase: str,
        wait_seconds: float,
        resets_at: datetime | None = None,
        source: str = "fallback",
        agent_name: str | None = None,
        message: str = "",
    ):
        self.phase = phase
        self.wait_seconds = wait_seconds
        self.resets_at = resets_at
        self.source = source
        self.agent_name = agent_name
        super().__init__(
            message
            or f"Rate limit hit at {phase}; pausing {wait_seconds:.0f}s"
        )


class RateLimitError(AgentRunError):
    """Raised when rate limit retries are exhausted."""

    def __init__(
        self,
        agent_name: str,
        message: str,
        attempts: int,
        exit_code: int | None = None,
    ):
        self.attempts = attempts
        super().__init__(agent_name=agent_name, message=message, exit_code=exit_code)


class DocumentError(AgenticDevError):
    """Raised for document read/write errors."""


class LockError(AgenticDevError):
    """Raised for file locking failures."""


class MCPPrerequisiteError(AgenticDevError):
    """Raised when required MCP services are not configured."""

    def __init__(self, services: list[str], message: str = ""):
        self.services = services
        detail = ", ".join(services)
        super().__init__(
            message
            or f"MCP prerequisites not met for: {detail}"
        )
