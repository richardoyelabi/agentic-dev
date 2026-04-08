"""Custom exception hierarchy for the agentic-dev agency."""


class AgenticDevError(Exception):
    """Base exception for all agentic-dev errors."""


class AgentRunError(AgenticDevError):
    """Raised when a Claude agent run fails."""

    def __init__(self, agent_name: str, message: str, exit_code: int | None = None):
        self.agent_name = agent_name
        self.exit_code = exit_code
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
