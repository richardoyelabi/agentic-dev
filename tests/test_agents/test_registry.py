"""Tests for the AgentRegistry."""

from pathlib import Path

import pytest

from agentic_dev.agents.registry import AgentRegistry
from agentic_dev.config import AGENT_DEFINITIONS_DIR
from agentic_dev.exceptions import AgentDefinitionError


@pytest.fixture
def registry() -> AgentRegistry:
    """Load the registry from the real definitions directory."""
    return AgentRegistry(definitions_dir=AGENT_DEFINITIONS_DIR)


class TestAgentRegistry:
    """Tests for AgentRegistry loading and lookup."""

    def test_loads_all_definitions(self, registry: AgentRegistry):
        agents = registry.list_agents()
        assert len(agents) == 23

    def test_get_returns_correct_agent(self, registry: AgentRegistry):
        architect = registry.get("architect")
        assert architect.name == "architect"
        assert architect.team == "design_architecture"
        assert architect.claude.model == "opus"

    def test_get_raises_for_unknown_agent(self, registry: AgentRegistry):
        with pytest.raises(AgentDefinitionError):
            registry.get("nonexistent_agent")

    def test_list_by_team_design_architecture(self, registry: AgentRegistry):
        design_agents = registry.list_by_team("design_architecture")
        assert len(design_agents) == 8
        names = {a.name for a in design_agents}
        assert "input_processor" in names
        assert "input_updater" in names
        assert "architect" in names
        assert "sprint_planner_qa" in names

    def test_list_by_team_frontend(self, registry: AgentRegistry):
        frontend_agents = registry.list_by_team("frontend")
        assert len(frontend_agents) == 2
        names = {a.name for a in frontend_agents}
        assert names == {"frontend_developer", "frontend_qa"}

    def test_list_by_team_backend(self, registry: AgentRegistry):
        backend_agents = registry.list_by_team("backend")
        assert len(backend_agents) == 2
        names = {a.name for a in backend_agents}
        assert names == {"backend_developer", "backend_qa"}

    def test_list_by_team_integration(self, registry: AgentRegistry):
        integration_agents = registry.list_by_team("integration")
        assert len(integration_agents) == 2
        names = {a.name for a in integration_agents}
        assert names == {"integration", "integration_qa"}

    def test_list_by_team_qa(self, registry: AgentRegistry):
        qa_agents = registry.list_by_team("qa")
        assert len(qa_agents) == 1
        assert qa_agents[0].name == "uat"

    def test_list_by_team_returns_empty_for_unknown_team(
        self, registry: AgentRegistry
    ):
        agents = registry.list_by_team("nonexistent_team")
        assert agents == []

    def test_agent_fields_loaded_correctly(self, registry: AgentRegistry):
        frontend_dev = registry.get("frontend_developer")
        assert frontend_dev.claude.permission_mode == "bypassPermissions"
        assert frontend_dev.working_directory == "frontend"
        assert frontend_dev.qa_agent == "frontend_qa"
        assert "Bash" in frontend_dev.claude.allowed_tools
        assert frontend_dev.output_documents == []

    def test_developer_agents_have_documentation_constraint(
        self, registry: AgentRegistry
    ):
        for name in ["frontend_developer", "backend_developer"]:
            agent = registry.get(name)
            doc_constraints = [
                c for c in agent.constraints if "documentation" in c.lower()
            ]
            assert len(doc_constraints) >= 1, (
                f"{name} missing documentation constraint"
            )

    def test_qa_agents_have_documentation_constraint(
        self, registry: AgentRegistry
    ):
        for name in ["frontend_qa", "backend_qa"]:
            agent = registry.get(name)
            doc_constraints = [
                c for c in agent.constraints if "documentation" in c.lower()
            ]
            assert len(doc_constraints) >= 1, (
                f"{name} missing documentation verification constraint"
            )

    def test_invalid_definitions_dir_raises(self, tmp_path: Path):
        bad_yaml = tmp_path / "bad.yml"
        bad_yaml.write_text("name: 123\n")
        with pytest.raises(AgentDefinitionError):
            AgentRegistry(definitions_dir=tmp_path)
