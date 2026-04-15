"""Tests for the PromptRenderer class."""

from unittest.mock import patch

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
        "constraints": ["Minimalism first"],
        "correction_mode": False,
        "project_type": "fullstack",
    },
    "architect_qa.md.j2": {
        "features": "# Features Request\n## Feature: [F001] Auth",
        "structured_input": "# Structured Input\n- [F001] Auth",
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
    "structure_detector.md.j2": {},
    "spec_reverse_engineer.md.j2": {
        "target_spec_type": "frontend_spec",
        "existing_specs": "",
        "constraints": ["Follow ID conventions"],
        "correction_mode": False,
    },
    "spec_reverse_engineer_qa.md.j2": {
        "spec_output": "# Frontend Spec\n## Tech Stack\n- Framework: React 18",
        "target_spec_type": "frontend_spec",
    },
    "feature_extractor.md.j2": {
        "specs": "# Frontend Spec\n## Pages\n### [P001] Home",
        "constraints": ["Use EXISTING-F prefix"],
        "correction_mode": False,
    },
    "feature_extractor_qa.md.j2": {
        "features_output": "# Features Request\n## Feature: [EXISTING-F001] Auth",
    },
    "code_analyzer.md.j2": {
        "analysis_scope": "backend",
    },
    "drift_detector.md.j2": {
        "code_snapshots": "# Code Snapshot: Backend\n## Endpoints\n- GET /api/users",
        "spec_documents": "# API Contract\n## Endpoints\n### [E001] GET /api/users",
        "figma_analysis": "",
        "figma_sources": "",
        "sync_ignores": [],
    },
    "spec_updater.md.j2": {
        "spec_document": "# API Contract\n## Endpoints\n### [E001] GET /api/users",
        "resolved_items": [{"id": "DRIFT-001", "category": "in_code_not_spec", "description": "POST /api/webhooks"}],
        "constraints": ["Maintain ID sequences"],
    },
    "input_updater.md.j2": {
        "structured_input": "# Structured Input\n## Feature Requirements\n- [F001] Auth\n- [F002] Dashboard",
        "change_request": "Add a notifications feature and remove the dashboard",
        "constraints": ["Preserve existing features"],
    },
    "spec_diff.md.j2": {
        "old_structured_input": "# Structured Input\n## Feature Requirements\n- [F001] Auth\n- [F002] Dashboard",
        "new_structured_input": "# Structured Input\n## Feature Requirements\n- [F001] Auth with OAuth2\n- [F003] Settings page",
        "constraints": ["Identify all changes"],
    },
    "design_change_detection.md.j2": {
        "existing_spec": "# Frontend Spec\n## Pages\n### Home\n## Components\n### Button\n- **Border radius:** 4px",
        "figma_urls": "- https://figma.com/file/abc123/MyDesign",
        "sentinel": "NO_DESIGN_CHANGES",
    },
    "uat.md.j2": {
        "features": "# Features Request",
        "frontend_spec": "# Frontend Spec",
        "backend_spec": "# Backend Spec",
        "api_contract": "# API Contract",
        "sprint_plan": "# Sprint Plan",
        "change_request": "",
        "design_changes": "",
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
        assert "CLAUDE.md" in result

    def test_backend_developer_includes_documentation_section(self, real_renderer):
        result = real_renderer.render(
            "backend_developer.md.j2",
            AGENT_TEMPLATES["backend_developer.md.j2"],
        )
        assert "Documentation Requirements" in result
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "CLAUDE.md" in result

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

    def test_architect_renders_figma_sources_when_provided(self, real_renderer):
        """When figma_sources is provided, the Figma section renders."""
        result = real_renderer.render("architect.md.j2", {
            "features": "# Features Request\n## Feature: [F001] Auth",
            "structured_input": "# Structured Input\n- [F001] Auth",
            "figma_sources": "# Figma Sources\n- URL: https://figma.com/file/abc",
            "figma_mcp_available": "true",
            "constraints": [],
            "correction_mode": False,
            "project_type": "fullstack",
        })
        assert "Figma Design Reference" in result
        assert "figma.com/file/abc" in result

    def test_architect_omits_figma_section_when_absent(self, real_renderer):
        """When figma_sources is not provided, no Figma section renders."""
        result = real_renderer.render("architect.md.j2", {
            "features": "# Features Request\n## Feature: [F001] Auth",
            "structured_input": "# Structured Input\n- [F001] Auth",
            "constraints": [],
            "correction_mode": False,
            "project_type": "fullstack",
        })
        assert "Figma Design Reference" not in result

    def test_architect_qa_renders_figma_sources_when_provided(self, real_renderer):
        """When figma_sources is provided, the Figma section renders in QA."""
        result = real_renderer.render("architect_qa.md.j2", {
            "features": "# Features Request",
            "structured_input": "# Structured Input",
            "figma_sources": "# Figma Sources\n- URL: https://figma.com/file/xyz",
            "figma_mcp_available": "true",
            "architecture": "# Frontend Spec\n## Pages",
            "project_type": "fullstack",
        })
        assert "Figma Design Reference" in result
        assert "figma.com/file/xyz" in result

    def test_architect_qa_omits_figma_section_when_absent(self, real_renderer):
        """When figma_sources is not provided, no Figma section renders in QA."""
        result = real_renderer.render("architect_qa.md.j2", {
            "features": "# Features Request",
            "structured_input": "# Structured Input",
            "architecture": "# Frontend Spec\n## Pages",
            "project_type": "fullstack",
        })
        assert "Figma Design Reference" not in result

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


class TestFigmaPromptSections:
    """Verify frontend templates include Figma sections when figma_sources is provided."""

    def test_frontend_developer_includes_figma_section_when_available(self, real_renderer):
        context = {
            **AGENT_TEMPLATES["frontend_developer.md.j2"],
            "figma_sources": "# Figma Sources\n- URL: https://figma.com/file/abc",
            "figma_mcp_available": "true",
        }
        result = real_renderer.render("frontend_developer.md.j2", context)
        assert "Figma Design Reference" in result
        assert "visual source of truth" in result.lower()
        assert "figma.com/file/abc" in result

    def test_frontend_developer_excludes_figma_section_without_sources(self, real_renderer):
        result = real_renderer.render(
            "frontend_developer.md.j2",
            AGENT_TEMPLATES["frontend_developer.md.j2"],
        )
        assert "Figma Design Reference" not in result

    def test_frontend_developer_figma_mcp_unavailable_fallback(self, real_renderer):
        context = {
            **AGENT_TEMPLATES["frontend_developer.md.j2"],
            "figma_sources": "# Figma Sources\n- URL: https://figma.com/file/abc",
            "figma_mcp_available": "false",
        }
        result = real_renderer.render("frontend_developer.md.j2", context)
        assert "Figma Design Reference" in result
        assert "not available" in result.lower()

    def test_frontend_qa_includes_figma_section_when_available(self, real_renderer):
        context = {
            **AGENT_TEMPLATES["frontend_qa.md.j2"],
            "figma_sources": "# Figma Sources\n- URL: https://figma.com/file/abc",
            "figma_mcp_available": "true",
        }
        result = real_renderer.render("frontend_qa.md.j2", context)
        assert "Figma Design Reference" in result
        assert "figma.com/file/abc" in result

    def test_frontend_qa_includes_design_fidelity_criterion(self, real_renderer):
        context = {
            **AGENT_TEMPLATES["frontend_qa.md.j2"],
            "figma_sources": "# Figma Sources\n- URL: https://figma.com/file/abc",
            "figma_mcp_available": "true",
        }
        result = real_renderer.render("frontend_qa.md.j2", context)
        assert "match the design" in result.lower()

    def test_frontend_qa_excludes_figma_section_without_sources(self, real_renderer):
        result = real_renderer.render(
            "frontend_qa.md.j2",
            AGENT_TEMPLATES["frontend_qa.md.j2"],
        )
        assert "Figma Design Reference" not in result


class TestUpdateContextInTemplates:
    """Verify update context flows correctly through templates."""

    def test_backend_qa_includes_update_context_when_change_request_present(self, real_renderer):
        result = real_renderer.render("backend_qa.md.j2", {
            **AGENT_TEMPLATES["backend_qa.md.j2"],
            "change_request": "Change payment endpoint to /api/v2/payments",
        })
        assert "Update Context" in result
        assert "Change payment endpoint" in result
        assert "Additional review criteria for updates" in result

    def test_backend_qa_omits_update_context_without_change_request(self, real_renderer):
        result = real_renderer.render(
            "backend_qa.md.j2",
            AGENT_TEMPLATES["backend_qa.md.j2"],
        )
        assert "Additional review criteria for updates" not in result

    def test_frontend_qa_includes_update_context_when_change_request_present(self, real_renderer):
        result = real_renderer.render("frontend_qa.md.j2", {
            **AGENT_TEMPLATES["frontend_qa.md.j2"],
            "change_request": "Add dark mode toggle",
            "design_changes": "## Components\n- Button: color changed",
        })
        assert "Update Context" in result
        assert "Add dark mode toggle" in result
        assert "Design Changes" in result
        assert "Button: color changed" in result

    def test_integration_qa_includes_update_context_when_change_request_present(self, real_renderer):
        result = real_renderer.render("integration_qa.md.j2", {
            **AGENT_TEMPLATES["integration_qa.md.j2"],
            "change_request": "Switch from Stripe to PayPal",
        })
        assert "Update Context" in result
        assert "Switch from Stripe to PayPal" in result

    def test_sprint_planner_qa_includes_update_correctness_criterion(self, real_renderer):
        result = real_renderer.render("sprint_planner_qa.md.j2", {
            **AGENT_TEMPLATES["sprint_planner_qa.md.j2"],
            "change_request": "Add notifications feature",
        })
        assert "Update correctness" in result
        assert "EXISTING-F" in result
        assert "DELETED-F" in result

    def test_sprint_planner_qa_omits_update_criterion_without_change_request(self, real_renderer):
        result = real_renderer.render(
            "sprint_planner_qa.md.j2",
            AGENT_TEMPLATES["sprint_planner_qa.md.j2"],
        )
        assert "Update correctness" not in result


class TestDeveloperTemplateReordering:
    """Verify update context appears before specs in developer templates."""

    def test_backend_developer_update_context_before_spec(self, real_renderer):
        result = real_renderer.render("backend_developer.md.j2", {
            **AGENT_TEMPLATES["backend_developer.md.j2"],
            "change_request": "CHANGE_MARKER_HERE",
        })
        update_pos = result.index("CHANGE_MARKER_HERE")
        spec_pos = result.index("# Input: Backend Spec")
        assert update_pos < spec_pos, (
            "Update context should appear before the backend spec input section"
        )

    def test_frontend_developer_update_context_before_spec(self, real_renderer):
        result = real_renderer.render("frontend_developer.md.j2", {
            **AGENT_TEMPLATES["frontend_developer.md.j2"],
            "change_request": "CHANGE_MARKER_HERE",
        })
        update_pos = result.index("CHANGE_MARKER_HERE")
        spec_pos = result.index("# Input: Frontend Spec")
        assert update_pos < spec_pos, (
            "Update context should appear before the frontend spec input section"
        )


class TestCodeCorrectionInstructions:
    """Verify developer templates use code-specific correction instructions."""

    def test_backend_developer_uses_code_correction_partial(self, real_renderer):
        result = real_renderer.render("backend_developer.md.j2", {
            **AGENT_TEMPLATES["backend_developer.md.j2"],
            "correction_mode": True,
            "previous_output": "Previous summary",
            "qa_feedback": "Fix the auth middleware",
        })
        assert "already on the filesystem" in result
        assert "targeted fixes" in result
        assert "produce a corrected version" not in result

    def test_frontend_developer_uses_code_correction_partial(self, real_renderer):
        result = real_renderer.render("frontend_developer.md.j2", {
            **AGENT_TEMPLATES["frontend_developer.md.j2"],
            "correction_mode": True,
            "previous_output": "Previous summary",
            "qa_feedback": "Fix the component styling",
        })
        assert "already on the filesystem" in result
        assert "targeted fixes" in result

    def test_integration_uses_code_correction_partial(self, real_renderer):
        result = real_renderer.render("integration.md.j2", {
            **AGENT_TEMPLATES["integration.md.j2"],
            "correction_mode": True,
            "previous_output": "Previous summary",
            "qa_feedback": "Fix the Stripe webhook handler",
        })
        assert "already on the filesystem" in result
        assert "targeted fixes" in result


class TestDeletedFeatureHandling:
    """Verify DELETED-F markers are handled across templates."""

    def test_input_updater_mentions_deleted_prefix(self, real_renderer):
        result = real_renderer.render(
            "input_updater.md.j2",
            AGENT_TEMPLATES["input_updater.md.j2"],
        )
        assert "[DELETED-F...]" in result
        assert "Delete the feature entry entirely" not in result

    def test_sprint_planner_mentions_deleted_features(self, real_renderer):
        result = real_renderer.render(
            "sprint_planner.md.j2",
            AGENT_TEMPLATES["sprint_planner.md.j2"],
        )
        assert "DELETED-F" in result
        assert "cleanup" in result.lower()

    def test_update_context_mentions_deleted_features(self, real_renderer):
        result = real_renderer.render("backend_developer.md.j2", {
            **AGENT_TEMPLATES["backend_developer.md.j2"],
            "change_request": "Remove the payment feature",
        })
        assert "DELETED-F" in result

    def test_update_qa_context_mentions_deleted_features(self, real_renderer):
        result = real_renderer.render("backend_qa.md.j2", {
            **AGENT_TEMPLATES["backend_qa.md.j2"],
            "change_request": "Remove the payment feature",
        })
        assert "DELETED-F" in result
        assert "dangling references" in result


class TestUATRuntimeVerification:
    """Verify UAT template includes runtime verification section."""

    def test_uat_includes_runtime_verification(self, real_renderer):
        result = real_renderer.render(
            "uat.md.j2",
            AGENT_TEMPLATES["uat.md.j2"],
        )
        assert "Runtime Verification" in result
        assert "Run the test suites" in result
        assert "Bash" in result

    def test_uat_includes_regression_check_with_change_request(self, real_renderer):
        result = real_renderer.render("uat.md.j2", {
            **AGENT_TEMPLATES["uat.md.j2"],
            "change_request": "Update payment endpoint",
        })
        assert "Regression check" in result


class TestIntegrationTemplatePartials:
    """Verify integration template includes sprint context and update context."""

    def test_integration_includes_sprint_context(self, real_renderer):
        result = real_renderer.render("integration.md.j2", {
            **AGENT_TEMPLATES["integration.md.j2"],
        })
        assert "Current Sprint Scope" in result

    def test_integration_includes_update_context_when_present(self, real_renderer):
        result = real_renderer.render("integration.md.j2", {
            **AGENT_TEMPLATES["integration.md.j2"],
            "change_request": "Switch to PayPal SDK",
        })
        assert "Update Context" in result
        assert "Switch to PayPal SDK" in result


class TestPriorSprintSummaries:
    """Verify developer templates render prior sprint summaries."""

    def test_backend_developer_renders_prior_summaries(self, real_renderer):
        result = real_renderer.render("backend_developer.md.j2", {
            **AGENT_TEMPLATES["backend_developer.md.j2"],
            "prior_sprint_summaries": "### Sprint 1 (backend)\n- Created User model\n- 5 tests passing",
        })
        assert "Prior Sprint Context" in result
        assert "Created User model" in result

    def test_backend_developer_omits_prior_summaries_when_absent(self, real_renderer):
        result = real_renderer.render(
            "backend_developer.md.j2",
            AGENT_TEMPLATES["backend_developer.md.j2"],
        )
        assert "Prior Sprint Context" not in result


class TestPromptSizeWarning:
    """Verify prompt size guardrails emit warnings for large prompts."""

    def test_large_prompt_emits_warning(self, real_renderer):
        """A prompt exceeding 70% of context window should log a warning."""
        # 200k tokens / 1.5 tokens-per-word * 0.7 threshold ≈ 93k words
        large_spec = " ".join(["word"] * 100_000)
        with patch("agentic_dev.prompts.renderer.emit") as mock_emit:
            real_renderer.render_agent_prompt(
                template_name="backend_developer.md.j2",
                input_documents={
                    "backend_spec": large_spec,
                    "api_contract": "# API Contract",
                    "sprint_scope": "Sprint 1",
                },
                constraints=["TDD"],
            )
            # Find the warning-level emit call
            warning_calls = [
                c for c in mock_emit.call_args_list
                if hasattr(c[0][1], "level") and c[0][1].level == "WARNING"
            ]
            assert len(warning_calls) >= 1
            assert "approaching context window" in warning_calls[0][0][1].message

    def test_normal_prompt_does_not_emit_warning(self, real_renderer):
        """A normal-sized prompt should not emit a warning."""
        with patch("agentic_dev.prompts.renderer.emit") as mock_emit:
            real_renderer.render_agent_prompt(
                template_name="backend_developer.md.j2",
                input_documents={
                    "backend_spec": "# Backend Spec\n## Models\n- User",
                    "api_contract": "# API Contract",
                    "sprint_scope": "Sprint 1",
                },
                constraints=["TDD"],
            )
            warning_calls = [
                c for c in mock_emit.call_args_list
                if hasattr(c[0][1], "level") and c[0][1].level == "WARNING"
            ]
            assert len(warning_calls) == 0

    def test_token_estimate_uses_word_count(self, real_renderer):
        """Token estimation should use word count * 1.5, not char count / 4."""
        # 10 words * 1.5 = 15 estimated tokens (well under threshold)
        # But 50 chars / 4 = 12.5 tokens — both are low, so verify via
        # a case where the two methods diverge significantly.
        # "x" * 600_000 is 1 word -> 1.5 tokens (no warning)
        # Under old method: 600_000 / 4 = 150,000 tokens (would warn)
        single_long_word = "x" * 600_000
        with patch("agentic_dev.prompts.renderer.emit") as mock_emit:
            real_renderer.render_agent_prompt(
                template_name="backend_developer.md.j2",
                input_documents={
                    "backend_spec": single_long_word,
                    "api_contract": "# API",
                    "sprint_scope": "Sprint 1",
                },
                constraints=["TDD"],
            )
            warning_calls = [
                c for c in mock_emit.call_args_list
                if hasattr(c[0][1], "level") and c[0][1].level == "WARNING"
            ]
            # Word-based: ~few words -> no warning. Old char-based would warn.
            assert len(warning_calls) == 0

    def test_warning_message_includes_token_estimate(self, real_renderer):
        """Warning message should contain the estimated token count."""
        large_spec = " ".join(["word"] * 100_000)
        with patch("agentic_dev.prompts.renderer.emit") as mock_emit:
            real_renderer.render_agent_prompt(
                template_name="backend_developer.md.j2",
                input_documents={
                    "backend_spec": large_spec,
                    "api_contract": "# API",
                    "sprint_scope": "Sprint 1",
                },
                constraints=["TDD"],
            )
            warning_calls = [
                c for c in mock_emit.call_args_list
                if hasattr(c[0][1], "level") and c[0][1].level == "WARNING"
            ]
            assert len(warning_calls) >= 1
            msg = warning_calls[0][0][1].message
            assert "tokens" in msg
            assert "approaching context window" in msg

    def test_prompt_rendered_event_emitted_for_normal_prompt(self, real_renderer):
        """A PromptRenderedEvent should be emitted for every render."""
        with patch("agentic_dev.prompts.renderer.emit") as mock_emit:
            real_renderer.render_agent_prompt(
                template_name="backend_developer.md.j2",
                input_documents={
                    "backend_spec": "# Spec",
                    "api_contract": "# API",
                    "sprint_scope": "Sprint 1",
                },
                constraints=["TDD"],
            )
            rendered_events = [
                c for c in mock_emit.call_args_list
                if hasattr(c[0][1], "event_type")
                and c[0][1].event_type == "prompt_rendered"
            ]
            assert len(rendered_events) >= 1
            event = rendered_events[0][0][1]
            assert event.template_name == "backend_developer.md.j2"
            assert event.output_length > 0
