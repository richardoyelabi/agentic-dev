"""Pydantic models for agent definition YAML files."""

from pydantic import BaseModel


class ClaudeConfig(BaseModel):
    """Configuration for the Claude CLI invocation."""

    model: str
    permission_mode: str
    allowed_tools: list[str]
    max_budget_usd: float
    use_bare_mode: bool = True
    max_turns: int = 50
    timeout_s: int | None = None


class AgentDefinition(BaseModel):
    """A single agent definition parsed from a YAML file."""

    name: str
    description: str
    team: str
    claude: ClaudeConfig
    prompt_template: str
    input_documents: list[str]
    output_documents: list[str] = []
    qa_agent: str | None = None
    working_directory: str = "."
    constraints: list[str] = []
