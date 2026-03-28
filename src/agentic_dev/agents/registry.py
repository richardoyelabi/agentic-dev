"""Agent registry that loads and indexes agent definitions from YAML files."""

from pathlib import Path

import yaml

from agentic_dev.agents.base import AgentDefinition
from agentic_dev.config import AGENT_DEFINITIONS_DIR
from agentic_dev.exceptions import AgentDefinitionError


class AgentRegistry:
    """Loads all agent definition YAML files and provides lookup methods."""

    def __init__(self, definitions_dir: Path | None = None) -> None:
        self._definitions_dir = definitions_dir or AGENT_DEFINITIONS_DIR
        self._agents: dict[str, AgentDefinition] = {}
        self._load_definitions()

    def _load_definitions(self) -> None:
        """Iterate over YAML files in the definitions directory and parse them."""
        for yaml_path in sorted(self._definitions_dir.glob("*.yml")):
            try:
                raw = yaml.safe_load(yaml_path.read_text())
                agent = AgentDefinition(**raw)
                self._agents[agent.name] = agent
            except Exception as exc:
                raise AgentDefinitionError(
                    yaml_path.stem,
                    f"Failed to load {yaml_path.name}: {exc}",
                ) from exc

    def get(self, name: str) -> AgentDefinition:
        """Look up an agent by name. Raises AgentDefinitionError if not found."""
        if name not in self._agents:
            raise AgentDefinitionError(name, "Agent not found in registry")
        return self._agents[name]

    def list_agents(self) -> list[AgentDefinition]:
        """Return all loaded agent definitions."""
        return list(self._agents.values())

    def list_by_team(self, team: str) -> list[AgentDefinition]:
        """Return agent definitions filtered by team."""
        return [a for a in self._agents.values() if a.team == team]
