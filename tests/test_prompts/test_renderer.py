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

    def test_render_with_undefined_variable_raises_error(self, renderer):
        """Undefined template variables should raise an error, not silently render empty."""
        with pytest.raises(TemplateRenderError):
            renderer.render("simple.md.j2", {"name": "Alice"})

    def test_render_with_empty_string_value_succeeds(self, renderer):
        """Explicitly passing an empty string is fine — only undefined variables error."""
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

    def test_extra_context_merged_into_template(self, templates_dir, renderer):
        """extra_context should be available as template variables."""
        typed_template = templates_dir / "typed.md.j2"
        typed_template.write_text(
            "Type: {{ project_type }}\n"
            "Input: {{ user_input }}\n"
            "{% for c in constraints %}- {{ c }}\n{% endfor %}"
        )
        result = renderer.render_agent_prompt(
            template_name="typed.md.j2",
            input_documents={"user_input": "Build an app"},
            constraints=["TDD"],
            extra_context={"project_type": "frontend_only"},
        )
        assert "Type: frontend_only" in result
        assert "Build an app" in result

    def test_extra_context_none_does_not_break(self, renderer):
        result = renderer.render_agent_prompt(
            template_name="agent.md.j2",
            input_documents={"user_input": "Build an app"},
            constraints=[],
            extra_context=None,
        )
        assert "Build an app" in result


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
        "features": "# Features Request\n## Feature: [F001] Auth",
    },
    "architect.md.j2": {
        "features": "# Features Request\n## Feature: [F001] Auth",
        "structured_input": "# Structured Input\n- [F001] Auth",
        "design_analyses": "",
        "constraints": ["Minimalism first"],
        "correction_mode": False,
        "project_type": "fullstack",
    },
    "architect_qa.md.j2": {
        "features": "# Features Request\n## Feature: [F001] Auth",
        "structured_input": "# Structured Input\n- [F001] Auth",
        "design_analyses": "",
        "architecture": "# Frontend Spec\n## Pages\n# Backend Spec\n## Models\n# API Contract\n## Endpoints",
        "project_type": "fullstack",
    },
    "sprint_planner.md.j2": {
        "features": "# Features Request\n## Feature: [F001] Auth",
        "frontend_spec": "# Frontend Spec",
        "backend_spec": "# Backend Spec",
        "api_contract": "# API Contract",
        "constraints": ["Order by dependency"],
        "correction_mode": False,
    },
    "sprint_planner_qa.md.j2": {
        "features": "# Features Request",
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
        "sprint_scope": "# Sprint Scope",
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
        "sprint_scope": "# Sprint Scope",
    },
    "integration.md.j2": {
        "api_contract": "# API Contract",
        "sprint_scope": "# Sprint Scope\n## Sprint 1",
        "constraints": ["Follow SDK best practices"],
        "correction_mode": False,
    },
    "integration_qa.md.j2": {
        "api_contract": "# API Contract",
        "sprint_scope": "# Sprint Scope",
        "integration_guide": "# Integration Guide\n## Service: Stripe",
    },
    "uat.md.j2": {
        "features": "# Features Request",
        "frontend_spec": "# Frontend Spec",
        "backend_spec": "# Backend Spec",
        "api_contract": "# API Contract",
        "sprint_plan": "# Sprint Plan",
    },
}


class TestDocumentationRequirements:
    """Verify developer templates include documentation requirements and QA templates include documentation criterion."""

    def test_frontend_developer_includes_documentation_section(self, real_renderer):
        result = real_renderer.render(
            "frontend_developer.md.j2",
            AGENT_TEMPLATES["frontend_developer.md.j2"],
        )
        assert "Documentation Requirements" in result
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result

    def test_backend_developer_includes_documentation_section(self, real_renderer):
        result = real_renderer.render(
            "backend_developer.md.j2",
            AGENT_TEMPLATES["backend_developer.md.j2"],
        )
        assert "Documentation Requirements" in result
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result

    def test_frontend_qa_includes_documentation_criterion(self, real_renderer):
        result = real_renderer.render(
            "frontend_qa.md.j2",
            AGENT_TEMPLATES["frontend_qa.md.j2"],
        )
        assert "Is it documented?" in result
        assert "### Documentation" in result

    def test_backend_qa_includes_documentation_criterion(self, real_renderer):
        result = real_renderer.render(
            "backend_qa.md.j2",
            AGENT_TEMPLATES["backend_qa.md.j2"],
        )
        assert "Is it documented?" in result
        assert "### Documentation" in result


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


class TestTemplateVariablesMatchOrchestratorKeys:
    """Verify templates render content using the variable names the orchestrator passes.

    The orchestrator (engine.py, sprint_runner.py) passes input_docs with specific
    keys (e.g. 'features', 'sprint_scope'). Templates must use these exact keys
    as Jinja2 variables, otherwise the content renders as empty.
    """

    MARKER = "UNIQUE_CONTENT_MARKER_12345"

    def test_architect_receives_features_not_features_request(self, real_renderer):
        """engine.py passes 'features' key, not 'features_request'."""
        result = real_renderer.render("architect.md.j2", {
            "features": f"Features: {self.MARKER}",
            "structured_input": "structured input content",
            "design_analyses": "",
            "constraints": [],
            "correction_mode": False,
            "project_type": "fullstack",
        })
        assert self.MARKER in result, (
            "architect.md.j2 did not render 'features' variable — "
            "likely still using 'features_request'"
        )

    def test_architect_qa_receives_features_and_architecture(self, real_renderer):
        """engine.py passes 'features' and qa_cycle adds 'architecture' output."""
        result = real_renderer.render("architect_qa.md.j2", {
            "features": f"Features: {self.MARKER}",
            "structured_input": "structured input",
            "design_analyses": "",
            "architecture": f"Architecture: {self.MARKER}",
            "project_type": "fullstack",
        })
        assert self.MARKER in result, (
            "architect_qa.md.j2 did not render 'features' or 'architecture' — "
            "likely still using 'features_request' and separate spec variables"
        )

    def test_feature_analyst_qa_receives_features(self, real_renderer):
        """qa_cycle passes output as 'features' key (the output_doc_name)."""
        result = real_renderer.render("feature_analyst_qa.md.j2", {
            "structured_input": "structured input",
            "features": f"Features: {self.MARKER}",
        })
        assert self.MARKER in result, (
            "feature_analyst_qa.md.j2 did not render 'features' — "
            "likely still using 'features_request'"
        )

    def test_sprint_planner_receives_features(self, real_renderer):
        """engine.py passes 'features' key, not 'features_request'."""
        result = real_renderer.render("sprint_planner.md.j2", {
            "features": f"Features: {self.MARKER}",
            "frontend_spec": "frontend spec",
            "backend_spec": "backend spec",
            "api_contract": "api contract",
            "constraints": [],
            "correction_mode": False,
        })
        assert self.MARKER in result, (
            "sprint_planner.md.j2 did not render 'features' — "
            "likely still using 'features_request'"
        )

    def test_sprint_planner_qa_receives_features(self, real_renderer):
        """engine.py passes 'features' key, not 'features_request'."""
        result = real_renderer.render("sprint_planner_qa.md.j2", {
            "features": f"Features: {self.MARKER}",
            "sprint_plan": "sprint plan content",
        })
        assert self.MARKER in result, (
            "sprint_planner_qa.md.j2 did not render 'features' — "
            "likely still using 'features_request'"
        )

    def test_backend_qa_receives_sprint_scope(self, real_renderer):
        """sprint_runner.py passes 'sprint_scope', not 'sprint_plan'."""
        result = real_renderer.render("backend_qa.md.j2", {
            "backend_spec": "backend spec",
            "api_contract": "api contract",
            "sprint_scope": f"Sprint scope: {self.MARKER}",
        })
        assert self.MARKER in result, (
            "backend_qa.md.j2 did not render 'sprint_scope' — "
            "likely still using 'sprint_plan'"
        )

    def test_frontend_qa_receives_sprint_scope(self, real_renderer):
        """sprint_runner.py passes 'sprint_scope', not 'sprint_plan'."""
        result = real_renderer.render("frontend_qa.md.j2", {
            "frontend_spec": "frontend spec",
            "api_contract": "api contract",
            "sprint_scope": f"Sprint scope: {self.MARKER}",
        })
        assert self.MARKER in result, (
            "frontend_qa.md.j2 did not render 'sprint_scope' — "
            "likely still using 'sprint_plan'"
        )

    def test_integration_receives_sprint_scope(self, real_renderer):
        """sprint_runner.py passes 'sprint_scope', not 'sprint_plan'."""
        result = real_renderer.render("integration.md.j2", {
            "api_contract": "api contract",
            "sprint_scope": f"Sprint scope: {self.MARKER}",
            "constraints": [],
            "correction_mode": False,
        })
        assert self.MARKER in result, (
            "integration.md.j2 did not render 'sprint_scope' — "
            "likely still using 'sprint_plan'"
        )

    def test_integration_qa_receives_sprint_scope(self, real_renderer):
        """sprint_runner.py passes 'sprint_scope', not 'sprint_plan'."""
        result = real_renderer.render("integration_qa.md.j2", {
            "api_contract": "api contract",
            "sprint_scope": f"Sprint scope: {self.MARKER}",
            "integration_guide": "integration guide content",
        })
        assert self.MARKER in result, (
            "integration_qa.md.j2 did not render 'sprint_scope' — "
            "likely still using 'sprint_plan'"
        )

    def test_architect_renders_design_analyses_when_non_empty(self, real_renderer):
        """When design_analyses is non-empty, the section renders in the template."""
        result = real_renderer.render("architect.md.j2", {
            "features": "# Features Request\n## Feature: [F001] Auth",
            "structured_input": "# Structured Input\n- [F001] Auth",
            "design_analyses": "## Design Tokens\nColors: blue-500, gray-100",
            "constraints": [],
            "correction_mode": False,
            "project_type": "fullstack",
        })
        assert "Design Analyses (from Figma)" in result
        assert "blue-500" in result

    def test_architect_omits_design_analyses_when_empty(self, real_renderer):
        """When design_analyses is empty string, no design section renders."""
        result = real_renderer.render("architect.md.j2", {
            "features": "# Features Request\n## Feature: [F001] Auth",
            "structured_input": "# Structured Input\n- [F001] Auth",
            "design_analyses": "",
            "constraints": [],
            "correction_mode": False,
            "project_type": "fullstack",
        })
        assert "Design Analyses" not in result

    def test_architect_qa_renders_design_analyses_when_non_empty(self, real_renderer):
        """When design_analyses is non-empty, the section renders in the QA template."""
        result = real_renderer.render("architect_qa.md.j2", {
            "features": "# Features Request",
            "structured_input": "# Structured Input",
            "design_analyses": "## Components\nNavbar, Footer",
            "architecture": "# Frontend Spec\n## Pages",
            "project_type": "fullstack",
        })
        assert "Design Analyses (from Figma)" in result
        assert "Navbar, Footer" in result

    def test_architect_qa_omits_design_analyses_when_empty(self, real_renderer):
        """When design_analyses is empty string, no design section renders in QA."""
        result = real_renderer.render("architect_qa.md.j2", {
            "features": "# Features Request",
            "structured_input": "# Structured Input",
            "design_analyses": "",
            "architecture": "# Frontend Spec\n## Pages",
            "project_type": "fullstack",
        })
        assert "Design Analyses" not in result

    def test_uat_receives_features(self, real_renderer):
        """engine.py passes 'features' key, not 'features_request'."""
        result = real_renderer.render("uat.md.j2", {
            "features": f"Features: {self.MARKER}",
            "frontend_spec": "frontend spec",
            "backend_spec": "backend spec",
            "api_contract": "api contract",
            "sprint_plan": "sprint plan",
        })
        assert self.MARKER in result, (
            "uat.md.j2 did not render 'features' — "
            "likely still using 'features_request'"
        )


class TestInputProcessorOnboardingGuidance:
    """Verify the Input Processor template includes guidance for handling onboarding context."""

    def _render(self, real_renderer, user_input="Build an app"):
        return real_renderer.render("input_processor.md.j2", {
            "user_input": user_input,
            "constraints": [],
        })

    def test_contains_codebase_handling_guidance(self, real_renderer):
        """Template should instruct the agent on how to handle codebase analysis sections."""
        result = self._render(real_renderer)
        assert "Source: Codebase" in result
        assert "tech stack" in result.lower()

    def test_contains_figma_handling_guidance(self, real_renderer):
        """Template should instruct the agent on how to handle Figma design sections."""
        result = self._render(real_renderer)
        assert "Source: Figma Design" in result

    def test_mentions_existing_feature_prefix(self, real_renderer):
        """Template should instruct using [EXISTING] prefix for discovered features."""
        result = self._render(real_renderer)
        assert "[EXISTING]" in result

    def test_output_format_includes_patterns_and_conventions(self, real_renderer):
        """Output format should include a Patterns & Conventions subsection."""
        result = self._render(real_renderer)
        assert "Patterns & Conventions" in result

    def test_renders_with_embedded_onboarding_sources(self, real_renderer):
        """Template should render correctly when user_input contains embedded source sections."""
        user_input = (
            "Extend this application with a new admin panel\n\n"
            "---\n## Source: Codebase - Frontend React app\n"
            "**Path:** `/path/frontend`\n\n"
            "# Codebase Analysis\n## Tech Stack\n- Frontend: React 18\n- Backend: N/A\n\n"
            "---\n## Source: Figma Design - Admin dashboard\n"
            "**URL:** `https://figma.com/file/abc`\n\n"
            "# Design Analysis\n## Pages\n### Dashboard\n- Layout: sidebar + main"
        )
        result = self._render(real_renderer, user_input=user_input)
        assert len(result) > 0
        assert "React 18" in result
        assert "Admin dashboard" in result
        assert "{{" not in result
