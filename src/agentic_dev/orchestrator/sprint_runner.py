"""Sprint runner: executes a single sprint through backend -> frontend -> integration."""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from agentic_dev.agents.registry import AgentRegistry
from agentic_dev.claude.runner import ClaudeRunner
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import AgentRunError
from agentic_dev.orchestrator.qa_cycle import QACycleResult, run_qa_cycle
from agentic_dev.prompts.renderer import PromptRenderer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SprintResult:
    """Outcome of a full sprint execution."""

    sprint_number: int
    success: bool
    total_cost: float
    backend_result: QACycleResult | None = None
    frontend_result: QACycleResult | None = None
    integration_result: QACycleResult | None = None
    error: str | None = None


class SprintRunner:
    """Orchestrates backend, frontend, and optional integration QA cycles for a sprint."""

    def __init__(
        self,
        claude: ClaudeRunner,
        registry: AgentRegistry,
        doc_store: DocumentStore,
        prompt_renderer: PromptRenderer,
        project_dir: Path,
    ) -> None:
        self._claude = claude
        self._registry = registry
        self._doc_store = doc_store
        self._prompt_renderer = prompt_renderer
        self._project_dir = project_dir

    async def run_sprint(
        self,
        sprint_number: int,
        sprint_scope: str,
        needs_integration: bool = False,
    ) -> SprintResult:
        """Run a complete sprint: backend -> frontend -> optional integration.

        Args:
            sprint_number: The 1-based sprint number.
            sprint_scope: Sprint-specific scope extracted from the sprint plan.
            needs_integration: Whether to run the integration QA cycle.

        Returns:
            SprintResult with costs and success status.
        """
        try:
            return await self._execute_sprint(
                sprint_number, sprint_scope, needs_integration
            )
        except AgentRunError as exc:
            logger.error("Sprint %d failed: %s", sprint_number, exc)
            return SprintResult(
                sprint_number=sprint_number,
                success=False,
                total_cost=0.0,
                error=str(exc),
            )

    async def _execute_sprint(
        self,
        sprint_number: int,
        sprint_scope: str,
        needs_integration: bool,
    ) -> SprintResult:
        """Run the backend, frontend, and optional integration QA cycles."""
        backend_spec = self._doc_store.read("backend_spec")
        frontend_spec = self._doc_store.read("frontend_spec")
        api_contract = self._doc_store.read("api_contract")

        # Include checkpoint feedback as additional context if available
        extra_context: dict[str, str] = {}
        if self._doc_store.exists("checkpoint_feedback"):
            extra_context["user_feedback"] = self._doc_store.read("checkpoint_feedback")

        # Backend QA cycle
        backend_input_docs = {
            "backend_spec": backend_spec,
            "api_contract": api_contract,
            "sprint_scope": sprint_scope,
            **extra_context,
        }
        backend_result = await run_qa_cycle(
            claude=self._claude,
            action_agent=self._registry.get("backend_developer"),
            qa_agent=self._registry.get("backend_qa"),
            input_docs=backend_input_docs,
            output_doc_name=f"sprint_{sprint_number}_backend",
            workspace=self._project_dir / "backend",
            doc_store=self._doc_store,
            prompt_renderer=self._prompt_renderer,
        )

        # Frontend QA cycle
        frontend_input_docs = {
            "frontend_spec": frontend_spec,
            "api_contract": api_contract,
            "sprint_scope": sprint_scope,
            **extra_context,
        }
        frontend_result = await run_qa_cycle(
            claude=self._claude,
            action_agent=self._registry.get("frontend_developer"),
            qa_agent=self._registry.get("frontend_qa"),
            input_docs=frontend_input_docs,
            output_doc_name=f"sprint_{sprint_number}_frontend",
            workspace=self._project_dir / "frontend",
            doc_store=self._doc_store,
            prompt_renderer=self._prompt_renderer,
        )

        # Optional integration QA cycle
        integration_result = None
        if needs_integration:
            integration_input_docs = {
                "backend_spec": backend_spec,
                "frontend_spec": frontend_spec,
                "api_contract": api_contract,
                "sprint_scope": sprint_scope,
                **extra_context,
            }
            integration_result = await run_qa_cycle(
                claude=self._claude,
                action_agent=self._registry.get("integration"),
                qa_agent=self._registry.get("integration_qa"),
                input_docs=integration_input_docs,
                output_doc_name=f"sprint_{sprint_number}_integration",
                workspace=self._project_dir,
                doc_store=self._doc_store,
                prompt_renderer=self._prompt_renderer,
            )

        total_cost = (
            backend_result.action_cost
            + backend_result.qa_cost
            + backend_result.correction_cost
            + frontend_result.action_cost
            + frontend_result.qa_cost
            + frontend_result.correction_cost
        )
        if integration_result is not None:
            total_cost += (
                integration_result.action_cost
                + integration_result.qa_cost
                + integration_result.correction_cost
            )

        return SprintResult(
            sprint_number=sprint_number,
            success=True,
            total_cost=total_cost,
            backend_result=backend_result,
            frontend_result=frontend_result,
            integration_result=integration_result,
        )
