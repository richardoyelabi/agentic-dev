"""Tests for the AgentDefinition and ClaudeConfig Pydantic models."""

import pytest
from pydantic import ValidationError

from agentic_dev.agents.base import AgentDefinition, ClaudeConfig


class TestClaudeConfig:
    """Tests for ClaudeConfig model."""

    def test_defaults(self):
        config = ClaudeConfig(
            model="sonnet",
            permission_mode="plan",
            allowed_tools=["Read", "Glob"],
            max_budget_usd=1.00,
        )
        assert config.use_bare_mode is True
        assert config.max_turns == 50

    def test_override_defaults(self):
        config = ClaudeConfig(
            model="opus",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash", "Read"],
            max_budget_usd=5.00,
            use_bare_mode=False,
            max_turns=100,
        )
        assert config.use_bare_mode is False
        assert config.max_turns == 100

    def test_missing_required_field(self):
        with pytest.raises(ValidationError):
            ClaudeConfig(
                model="sonnet",
                permission_mode="plan",
                # missing allowed_tools and max_budget_usd
            )


class TestAgentDefinition:
    """Tests for AgentDefinition model."""

    def test_creation_with_valid_data(self):
        agent = AgentDefinition(
            name="test_agent",
            description="A test agent",
            team="test_team",
            claude=ClaudeConfig(
                model="sonnet",
                permission_mode="plan",
                allowed_tools=["Read", "Glob"],
                max_budget_usd=1.00,
            ),
            prompt_template="test.md.j2",
            input_documents=["doc_a"],
            output_documents=["doc_b"],
        )
        assert agent.name == "test_agent"
        assert agent.description == "A test agent"
        assert agent.team == "test_team"
        assert agent.claude.model == "sonnet"
        assert agent.prompt_template == "test.md.j2"
        assert agent.input_documents == ["doc_a"]
        assert agent.output_documents == ["doc_b"]

    def test_defaults(self):
        agent = AgentDefinition(
            name="minimal",
            description="Minimal agent",
            team="testing",
            claude=ClaudeConfig(
                model="sonnet",
                permission_mode="plan",
                allowed_tools=["Read"],
                max_budget_usd=0.50,
            ),
            prompt_template="minimal.md.j2",
            input_documents=["input"],
        )
        assert agent.output_documents == []
        assert agent.qa_agent is None
        assert agent.working_directory == "."
        assert agent.constraints == []

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            AgentDefinition(
                name="incomplete",
                # missing description, team, claude, prompt_template, input_documents
            )

    def test_qa_agent_can_be_none(self):
        agent = AgentDefinition(
            name="no_qa",
            description="Agent without QA",
            team="solo",
            claude=ClaudeConfig(
                model="sonnet",
                permission_mode="plan",
                allowed_tools=["Read"],
                max_budget_usd=1.00,
            ),
            prompt_template="no_qa.md.j2",
            input_documents=["input"],
            qa_agent=None,
        )
        assert agent.qa_agent is None

    def test_all_fields_populated(self):
        agent = AgentDefinition(
            name="full_agent",
            description="Fully configured agent",
            team="dev",
            claude=ClaudeConfig(
                model="opus",
                permission_mode="bypassPermissions",
                allowed_tools=["Bash", "Read", "Write"],
                max_budget_usd=5.00,
                use_bare_mode=False,
                max_turns=100,
            ),
            prompt_template="full.md.j2",
            input_documents=["spec", "contract"],
            output_documents=["report"],
            qa_agent="full_agent_qa",
            working_directory="frontend",
            constraints=["Do X", "Do Y"],
        )
        assert agent.qa_agent == "full_agent_qa"
        assert agent.working_directory == "frontend"
        assert len(agent.constraints) == 2
