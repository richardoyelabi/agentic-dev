"""Tests for the PromptRenderer class."""

import pytest

from agentic_dev.prompts.renderer import PromptRenderer, TemplateRenderError


@pytest.fixture()
def templates_dir(tmp_path):
    """Create a temporary templates directory with a simple test template."""
    template = tmp_path / "simple.md.j2"
    template.write_text("Hello, {{ name }}! You have {{ count }} items.")

    partials_dir = tmp_path / "_partials"
    partials_dir.mkdir()
    correction = partials_dir / "correction_instructions.md.j2"
    correction.write_text(
        "## Correction Instructions\n"
        "Previous: {{ previous_output }}\n"
        "Feedback: {{ qa_feedback }}"
    )

    agent_template = tmp_path / "agent.md.j2"
    agent_template.write_text(
        "# Input\n{{ user_input }}\n"
        "# Constraints\n"
        "{% for c in constraints %}- {{ c }}\n{% endfor %}"
        "{% if correction_mode %}\n"
        "{% include '_partials/correction_instructions.md.j2' %}\n"
        "{% endif %}"
    )
    return tmp_path


@pytest.fixture()
def renderer(templates_dir):
    """Create a PromptRenderer using the temporary templates directory."""
    return PromptRenderer(templates_dir=templates_dir)


@pytest.fixture()
def real_renderer():
    """Create a PromptRenderer using the actual project templates directory."""
    return PromptRenderer()


class TestRender:
    """Tests for the basic render method."""

    def test_render_simple_template(self, renderer):
        result = renderer.render("simple.md.j2", {"name": "Alice", "count": 5})
        assert "Hello, Alice!" in result
        assert "5 items" in result

    def test_render_missing_template_raises_error(self, renderer):
        with pytest.raises(TemplateRenderError, match="Template not found"):
            renderer.render("nonexistent.md.j2", {})

    def test_render_with_empty_context(self, renderer):
        """Template variables become empty strings when not provided."""
        result = renderer.render("simple.md.j2", {"name": "", "count": 0})
        assert "Hello, !" in result
        assert "0 items" in result


class TestRenderAgentPrompt:
    """Tests for the higher-level render_agent_prompt method."""

    def test_renders_with_input_documents(self, renderer):
        result = renderer.render_agent_prompt(
            template_name="agent.md.j2",
            input_documents={"user_input": "Build a todo app"},
            constraints=["Keep it simple", "Use REST"],
        )
        assert "Build a todo app" in result
        assert "Keep it simple" in result
        assert "Use REST" in result

    def test_renders_without_correction_mode(self, renderer):
        result = renderer.render_agent_prompt(
            template_name="agent.md.j2",
            input_documents={"user_input": "Build an app"},
            constraints=[],
        )
        assert "Correction Instructions" not in result

    def test_correction_mode_includes_previous_output_and_feedback(self, renderer):
        result = renderer.render_agent_prompt(
            template_name="agent.md.j2",
            input_documents={"user_input": "Build an app"},
            constraints=[],
            correction_mode=True,
            previous_output="Original output here",
            qa_feedback="Fix the naming conventions",
        )
        assert "Correction Instructions" in result
        assert "Original output here" in result
        assert "Fix the naming conventions" in result

    def test_correction_mode_defaults_to_empty_strings(self, renderer):
        result = renderer.render_agent_prompt(
            template_name="agent.md.j2",
            input_documents={"user_input": "Build an app"},
            constraints=[],
            correction_mode=True,
        )
        assert "Correction Instructions" in result

    def test_missing_template_raises_error(self, renderer):
        with pytest.raises(TemplateRenderError):
            renderer.render_agent_prompt(
                template_name="does_not_exist.md.j2",
                input_documents={},
                constraints=[],
            )


# Map each template to the context variables it requires.
AGENT_TEMPLATES = {
    "input_processor.md.j2": {
        "user_input": "Build a task management app with user auth.",
        "constraints": ["Keep it minimal"],
    },
    "feature_analyst.md.j2": {
        "structured_input": "# Structured Input\n## Feature Requirements\n- [F001] Auth",
        "constraints": ["Be thorough"],
        "correction_mode": False,
    },
    "feature_analyst_qa.md.j2": {
        "structured_input": "# Structured Input\n- [F001] Auth",
        "features_request": "# Features Request\n## Feature: [F001] Auth",
    },
    "architect.md.j2": {
        "features_request": "# Features Request\n## Feature: [F001] Auth",
        "structured_input": "# Structured Input\n- [F001] Auth",
        "constraints": ["Minimalism first"],
        "correction_mode": False,
    },
    "architect_qa.md.j2": {
        "features_request": "# Features Request\n## Feature: [F001] Auth",
        "structured_input": "# Structured Input\n- [F001] Auth",
        "frontend_spec": "# Frontend Spec\n## Pages",
        "backend_spec": "# Backend Spec\n## Models",
        "api_contract": "# API Contract\n## Endpoints",
    },
    "sprint_planner.md.j2": {
        "features_request": "# Features Request\n## Feature: [F001] Auth",
        "frontend_spec": "# Frontend Spec",
        "backend_spec": "# Backend Spec",
        "api_contract": "# API Contract",
        "constraints": ["Order by dependency"],
        "correction_mode": False,
    },
    "sprint_planner_qa.md.j2": {
        "features_request": "# Features Request",
        "sprint_plan": "# Sprint Plan\n## Sprint 1: Auth",
    },
    "frontend_developer.md.j2": {
        "frontend_spec": "# Frontend Spec\n## Pages",
        "api_contract": "# API Contract\n## Endpoints",
        "sprint_scope": "Sprint 1: Authentication",
        "constraints": ["Use TDD"],
        "correction_mode": False,
    },
    "frontend_qa.md.j2": {
        "frontend_spec": "# Frontend Spec",
        "api_contract": "# API Contract",
        "sprint_plan": "# Sprint Plan",
    },
    "backend_developer.md.j2": {
        "backend_spec": "# Backend Spec\n## Models",
        "api_contract": "# API Contract\n## Endpoints",
        "sprint_scope": "Sprint 1: Authentication",
        "constraints": ["Use TDD"],
        "correction_mode": False,
    },
    "backend_qa.md.j2": {
        "backend_spec": "# Backend Spec",
        "api_contract": "# API Contract",
        "sprint_plan": "# Sprint Plan",
    },
    "integration.md.j2": {
        "api_contract": "# API Contract",
        "sprint_plan": "# Sprint Plan\n## Sprint 1",
        "constraints": ["Follow SDK best practices"],
        "correction_mode": False,
    },
    "integration_qa.md.j2": {
        "api_contract": "# API Contract",
        "sprint_plan": "# Sprint Plan",
        "integration_guide": "# Integration Guide\n## Service: Stripe",
    },
    "uat.md.j2": {
        "features_request": "# Features Request",
        "frontend_spec": "# Frontend Spec",
        "backend_spec": "# Backend Spec",
        "api_contract": "# API Contract",
        "sprint_plan": "# Sprint Plan",
    },
}


class TestAgentTemplatesSmokeTest:
    """Smoke tests verifying all 14 agent templates render without errors."""

    @pytest.mark.parametrize(
        "template_name,context",
        list(AGENT_TEMPLATES.items()),
        ids=list(AGENT_TEMPLATES.keys()),
    )
    def test_template_renders_successfully(self, real_renderer, template_name, context):
        result = real_renderer.render(template_name, context)
        assert len(result) > 0, f"Template {template_name} produced empty output"
        # Verify no unresolved Jinja2 syntax leaked through
        assert "{{" not in result, f"Unresolved variable in {template_name}"
        assert "{%" not in result, f"Unresolved block in {template_name}"
