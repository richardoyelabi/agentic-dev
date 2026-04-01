"""Prompt rendering engine using Jinja2 templates."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

from agentic_dev.config import PROMPT_TEMPLATES_DIR
from agentic_dev.exceptions import AgenticDevError
from agentic_dev.logging import get_event_logger, emit
from agentic_dev.logging.events import PromptRenderedEvent

_event_log = get_event_logger("prompts")


class TemplateRenderError(AgenticDevError):
    """Raised when a prompt template cannot be rendered."""

    def __init__(self, template_name: str, message: str):
        self.template_name = template_name
        super().__init__(f"Failed to render template '{template_name}': {message}")


class PromptRenderer:
    """Renders Jinja2 prompt templates for agent invocations."""

    def __init__(self, templates_dir: Path | None = None):
        self._templates_dir = templates_dir or PROMPT_TEMPLATES_DIR
        self._env = Environment(
            loader=FileSystemLoader(str(self._templates_dir)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, template_name: str, context: dict) -> str:
        """Render a template with the given context dictionary."""
        try:
            template = self._env.get_template(template_name)
        except TemplateNotFound as exc:
            raise TemplateRenderError(template_name, "Template not found") from exc

        try:
            return template.render(**context)
        except Exception as exc:
            raise TemplateRenderError(template_name, str(exc)) from exc

    def render_agent_prompt(
        self,
        template_name: str,
        input_documents: dict[str, str],
        constraints: list[str],
        correction_mode: bool = False,
        previous_output: str | None = None,
        qa_feedback: str | None = None,
    ) -> str:
        """Render an agent prompt from standard agent inputs.

        Builds a context dictionary from the input documents, constraints,
        and optional correction-mode fields, then delegates to render().
        """
        context: dict = {
            **input_documents,
            "constraints": constraints,
            "correction_mode": correction_mode,
        }

        if correction_mode:
            context["previous_output"] = previous_output or ""
            context["qa_feedback"] = qa_feedback or ""

        result = self.render(template_name, context)
        emit(_event_log, PromptRenderedEvent(
            template_name=template_name,
            context_keys=list(input_documents.keys()),
            output_length=len(result),
            correction_mode=correction_mode,
            message=f"Rendered {template_name} ({len(result)} chars, correction={correction_mode})",
        ))
        return result
