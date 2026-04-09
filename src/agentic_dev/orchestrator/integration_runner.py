"""Integration runner: reruns integration stages for completed sprints."""

from __future__ import annotations

from pathlib import Path

from agentic_dev.agents.registry import AgentRegistry
from agentic_dev.claude.runner import ClaudeRunner
from agentic_dev.documents.store import DocumentStore
from agentic_dev.logging import get_event_logger, emit
from agentic_dev.logging.events import SprintPhaseEvent
from agentic_dev.mcp.claude_settings import discover_mcp_servers, find_server_for_service
from agentic_dev.orchestrator.qa_cycle import run_qa_cycle
from agentic_dev.orchestrator.sprint_runner import SprintResult
from agentic_dev.prompts.renderer import PromptRenderer
from agentic_dev.state.manager import StateManager
from agentic_dev.state.models import PipelineState, SprintState, SprintStatus

_event_log = get_event_logger("integration_runner")

_INTEGRATION_STATUSES = frozenset({
    SprintStatus.INTEGRATION,
    SprintStatus.INTEGRATION_QA,
    SprintStatus.INTEGRATION_CORRECTION,
})


class IntegrationRunner:
    """Runs only integration + integration_qa for a sprint.

    Used by the ``integrate`` CLI command to rerun integration stages
    after MCP services have been properly configured.
    """

    def __init__(
        self,
        claude: ClaudeRunner,
        registry: AgentRegistry,
        doc_store: DocumentStore,
        prompt_renderer: PromptRenderer,
        project_dir: Path,
        state_manager: StateManager,
        pipeline_state: PipelineState,
    ) -> None:
        self._claude = claude
        self._registry = registry
        self._doc_store = doc_store
        self._prompt_renderer = prompt_renderer
        self._project_dir = project_dir
        self._state_manager = state_manager
        self._pipeline_state = pipeline_state

    def _save_state(self) -> None:
        """Persist current pipeline state to disk."""
        self._state_manager.save(self._pipeline_state)

    def _check_mcp_availability(self, services: list[str]) -> None:
        """Log warnings for integration services missing from Claude Code settings."""
        if not services:
            return
        env = discover_mcp_servers(project_dir=self._project_dir)
        for service in services:
            if find_server_for_service(env, service) is None:
                _event_log.warning(
                    "No MCP server for '%s' found in Claude Code settings. "
                    "Run 'claude mcp add %s' to configure it.",
                    service,
                    service,
                )

    async def run_integration(self, sprint: SprintState) -> SprintResult:
        """Run integration QA cycle for a single sprint.

        Handles two scenarios:
        - Fresh run: sprint is COMPLETE, starts new integration session
        - Resume: sprint is stuck mid-integration, preserves session_id
        """
        sprint_number = sprint.sprint_number

        # Determine if resuming a crashed run
        resuming = sprint.status in _INTEGRATION_STATUSES
        session_id = sprint.integration_session_id if resuming else None

        # Transition to INTEGRATION status
        if not resuming:
            sprint.status = SprintStatus.INTEGRATION
        self._save_state()

        self._check_mcp_availability(sprint.integration_services)

        emit(
            _event_log,
            SprintPhaseEvent(
                sprint_number=sprint_number,
                sub_phase="integration",
                message=f"Sprint {sprint_number}: integration"
                + (" (resuming)" if resuming else ""),
            ),
        )

        # Gather input documents
        backend_spec = self._doc_store.read("backend_spec")
        frontend_spec = self._doc_store.read("frontend_spec")
        api_contract = self._doc_store.read("api_contract")
        sprint_scope = self._doc_store.read(f"sprint_{sprint_number}_scope")

        integration_input_docs = {
            "backend_spec": backend_spec,
            "frontend_spec": frontend_spec,
            "api_contract": api_contract,
            "sprint_scope": sprint_scope,
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
            qa_output_key="integration_guide",
            session_id=session_id,
            mcp_config=None,
        )

        sprint.integration_session_id = integration_result.session_id
        sprint.status = SprintStatus.COMPLETE
        self._save_state()

        return SprintResult(
            sprint_number=sprint_number,
            success=True,
            total_cost=integration_result.total_cost,
            integration_result=integration_result,
        )
