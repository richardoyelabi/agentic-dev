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
        assert len(design_agents) == 12
        names = {a.name for a in design_agents}
        assert "input_processor" in names
        assert "input_updater" in names
        assert "design_change_detection" in names
        assert "architect" in names
        assert "sprint_planner_qa" in names

    def test_list_by_team_development(self, registry: AgentRegistry):
        development_agents = registry.list_by_team("development")
        assert len(development_agents) == 2
        names = {a.name for a in development_agents}
        assert names == {"developer", "qa"}

    def test_list_by_team_integration(self, registry: AgentRegistry):
        integration_agents = registry.list_by_team("integration")
        assert len(integration_agents) == 2
        names = {a.name for a in integration_agents}
        assert names == {"integration", "integration_qa"}

    def test_list_by_team_qa(self, registry: AgentRegistry):
        qa_agents = registry.list_by_team("qa")
        assert len(qa_agents) == 7
        names = {a.name for a in qa_agents}
        assert names == {
            "uat_web",
            "uat_cli",
            "uat_desktop_electron",
            "uat_desktop_tauri",
            "uat_mobile",
            "uat_api",
            "uat_qa",
        }

    def test_list_by_team_returns_empty_for_unknown_team(
        self, registry: AgentRegistry
    ):
        agents = registry.list_by_team("nonexistent_team")
        assert agents == []

    def test_design_change_detection_agent_loaded(self, registry: AgentRegistry):
        agent = registry.get("design_change_detection")
        assert agent.team == "design_architecture"
        assert agent.claude.model == "sonnet"
        assert agent.claude.allowed_tools == ["Read", "Glob", "Grep"]
        assert agent.claude.max_turns == 15
        assert agent.input_documents == ["frontend_spec", "figma_sources"]
        assert agent.output_documents == ["design_changes"]
        assert agent.qa_agent is None
        assert len(agent.constraints) >= 4

    def test_agent_fields_loaded_correctly(self, registry: AgentRegistry):
        developer = registry.get("developer")
        assert developer.claude.permission_mode == "bypassPermissions"
        assert developer.qa_agent == "qa"
        assert "Bash" in developer.claude.allowed_tools
        assert developer.output_documents == []

    def test_developer_agents_have_documentation_constraint(
        self, registry: AgentRegistry
    ):
        agent = registry.get("developer")
        doc_constraints = [
            c for c in agent.constraints if "documentation" in c.lower()
        ]
        assert len(doc_constraints) >= 1, (
            "developer missing documentation constraint"
        )

    def test_qa_agents_have_documentation_constraint(
        self, registry: AgentRegistry
    ):
        agent = registry.get("qa")
        doc_constraints = [
            c for c in agent.constraints if "documentation" in c.lower()
        ]
        assert len(doc_constraints) >= 1, (
            "qa missing documentation verification constraint"
        )

    def test_invalid_definitions_dir_raises(self, tmp_path: Path):
        bad_yaml = tmp_path / "bad.yml"
        bad_yaml.write_text("name: 123\n")
        with pytest.raises(AgentDefinitionError):
            AgentRegistry(definitions_dir=tmp_path)
