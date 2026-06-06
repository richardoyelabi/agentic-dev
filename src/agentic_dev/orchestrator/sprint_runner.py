"""Sprint runner: executes a single sprint by iterating over the project's tracks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agentic_dev.agents.registry import AgentRegistry
from agentic_dev.claude.runner import ClaudeRunner
from agentic_dev.mcp.claude_settings import discover_mcp_servers, find_server_for_service
from agentic_dev.documents.scoping import extract_sprint_feature_ids, scope_spec_to_features
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import AgentRunError, RateLimitError
from agentic_dev.onboarding.figma import FigmaMCPNotConfigured, check_figma_mcp_available
from agentic_dev.logging import get_event_logger, emit
from agentic_dev.logging.context import get_run_context
from agentic_dev.logging.events import (
    SprintStartEvent,
    SprintPhaseEvent,
    SprintCompleteEvent,
    SprintFailedEvent,
)
from agentic_dev.orchestrator.qa_cycle import QACycleResult, run_qa_cycle
from agentic_dev.prompts.renderer import PromptRenderer
from agentic_dev.state.manager import StateManager
from agentic_dev.state.models import PipelineState, SprintState, SprintStatus
from agentic_dev.tracks import Track, TrackPhase, TrackProgress

_event_log = get_event_logger("sprint_runner")


@dataclass(frozen=True)
class SprintResult:
    """Outcome of a full sprint execution."""

    sprint_number: int
    success: bool
    total_cost: float
    track_results: dict[str, QACycleResult] = field(default_factory=dict)
    integration_result: QACycleResult | None = None
    error: str | None = None


class SprintRunner:
    """Orchestrates per-track QA cycles + optional integration for a sprint."""

    _SUMMARY_LINES_PER_SPRINT = 10

    def __init__(
        self,
        claude: ClaudeRunner,
        registry: AgentRegistry,
        doc_store: DocumentStore,
        prompt_renderer: PromptRenderer,
        project_dir: Path,
        tracks: list[Track],
        state_manager: StateManager | None = None,
        pipeline_state: PipelineState | None = None,
    ) -> None:
        self._claude = claude
        self._registry = registry
        self._doc_store = doc_store
        self._prompt_renderer = prompt_renderer
        self._project_dir = project_dir
        self._tracks = tracks
        self._state_manager = state_manager
        self._pipeline_state = pipeline_state

    def _save_state(self) -> None:
        if self._state_manager is not None and self._pipeline_state is not None:
            self._state_manager.save(self._pipeline_state)

    def _read_resume_cursor(self) -> tuple[str | None, str | None, int]:
        """The in-flight unit's resume cursor (session, stage, round).

        Only the first not-yet-complete track/integration reaches a QA cycle, so
        the pipeline-level cursor always belongs to it. Returns empties when no
        resume is pending."""
        st = self._pipeline_state
        if st is None:
            return None, None, 0
        return st.active_session_id, st.active_qa_stage, st.active_qa_round

    def _clear_resume_cursor(self) -> None:
        st = self._pipeline_state
        if st is not None:
            st.active_session_id = None
            st.active_qa_stage = None
            st.active_qa_round = 0

    def _qa_cursor_writer(self, on_stage=None):
        """``on_progress`` callback persisting the resume cursor as each QA-cycle
        stage runs (and, on failure, the failed session). ``on_stage`` lets the
        caller mirror the stage into its own dashboard status."""
        def _on_progress(stage: str, session_id: str | None, round_num: int) -> None:
            st = self._pipeline_state
            if st is not None:
                st.active_qa_stage = stage
                st.active_session_id = session_id
                st.active_qa_round = round_num
            if on_stage is not None:
                on_stage(stage)
            self._save_state()
        return _on_progress

    def _tracks_for_sprint(self, sprint_state: SprintState | None) -> list[Track]:
        if sprint_state is None or not sprint_state.tracks_in_scope:
            return list(self._tracks)
        scoped = set(sprint_state.tracks_in_scope)
        return [t for t in self._tracks if t.name in scoped]

    def _read_track_spec(self, track_name: str, feature_ids: set[str]) -> str:
        doc_name = f"{track_name}_spec"
        if not self._doc_store.exists(doc_name):
            return ""
        return scope_spec_to_features(self._doc_store.read(doc_name), feature_ids)

    def _update_rolling_summary(self, sprint_number: int) -> None:
        parts: list[str] = []
        suffixes = [t.name for t in self._tracks] + ["integration"]
        for suffix in suffixes:
            doc_name = f"sprint_{sprint_number}_{suffix}"
            if not self._doc_store.exists(doc_name):
                continue
            content = self._doc_store.read(doc_name)
            lines = content.strip().splitlines()
            tail = (
                lines[-self._SUMMARY_LINES_PER_SPRINT:]
                if len(lines) > self._SUMMARY_LINES_PER_SPRINT
                else lines
            )
            parts.append(
                f"### Sprint {sprint_number} ({suffix})\n" + "\n".join(tail)
            )
        if not parts:
            return
        new_entry = "\n\n".join(parts)
        if self._doc_store.exists("sprint_rolling_summary"):
            existing = self._doc_store.read("sprint_rolling_summary")
            updated = existing.rstrip() + "\n\n" + new_entry + "\n"
        else:
            updated = "## Prior Sprint Summaries\n\n" + new_entry + "\n"
        self._doc_store.write("sprint_rolling_summary", updated)

    def _resolve_integration_mcp_config(self, services: list[str]) -> Path | None:
        if not services:
            return None
        env = discover_mcp_servers(project_dir=self._project_dir)
        for service in services:
            if find_server_for_service(env, service) is None:
                _event_log.warning(
                    "No MCP server for '%s' found in Claude Code settings. "
                    "Run 'claude mcp add %s' to configure it.",
                    service,
                    service,
                )
        return None

    async def run_sprint(
        self,
        sprint_number: int,
        sprint_scope: str,
        sprint_state: SprintState | None = None,
        needs_integration: bool = False,
    ) -> SprintResult:
        """Run a complete sprint: per-track QA cycles, then optional integration."""
        partial_cost: list[float] = [0.0]
        ctx = get_run_context()
        start_time = datetime.now(timezone.utc)
        try:
            emit(_event_log, SprintStartEvent(
                sprint_number=sprint_number,
                sprint_name=sprint_scope,
                needs_integration=needs_integration,
                message=f"Sprint {sprint_number} started: {sprint_scope}",
            ))
            if ctx is not None:
                ctx.sprint_number = sprint_number

            result = await self._execute_sprint(
                sprint_number, sprint_scope, needs_integration, partial_cost,
                sprint_state,
            )

            self._update_rolling_summary(sprint_number)

            duration_s = (datetime.now(timezone.utc) - start_time).total_seconds()
            emit(_event_log, SprintCompleteEvent(
                sprint_number=sprint_number,
                success=True,
                total_cost=result.total_cost,
                duration_s=duration_s,
                message=f"Sprint {sprint_number} complete (${result.total_cost:.4f}, {duration_s:.1f}s)",
            ))
            if ctx is not None:
                ctx.sprint_number = None
            return result
        except RateLimitError:
            if ctx is not None:
                ctx.sprint_number = None
            raise
        except AgentRunError as exc:
            duration_s = (datetime.now(timezone.utc) - start_time).total_seconds()
            emit(_event_log, SprintFailedEvent(
                sprint_number=sprint_number,
                error=str(exc),
                partial_cost=partial_cost[0],
                level="ERROR",
                message=f"Sprint {sprint_number} failed: {exc}",
            ))
            if ctx is not None:
                ctx.sprint_number = None
            return SprintResult(
                sprint_number=sprint_number,
                success=False,
                total_cost=partial_cost[0],
                error=str(exc),
            )

    def _build_shared_context(self, sprint_state: SprintState | None) -> dict[str, str]:
        """Assemble per-sprint context shared across all tracks."""
        ctx: dict[str, str] = {}
        if self._doc_store.exists("sprint_rolling_summary"):
            ctx["prior_sprint_summaries"] = self._doc_store.read("sprint_rolling_summary")
        if self._doc_store.exists("checkpoint_feedback"):
            ctx["user_feedback"] = self._doc_store.read("checkpoint_feedback")
        if (
            self._pipeline_state is not None
            and self._pipeline_state.mode == "update"
            and self._doc_store.exists("user_input")
        ):
            ctx["change_request"] = self._doc_store.read("user_input")
        if self._doc_store.exists("spec_changes"):
            ctx["spec_changes"] = self._doc_store.read("spec_changes")
        if self._doc_store.exists("design_changes"):
            ctx["design_changes"] = self._doc_store.read("design_changes")
        if self._doc_store.exists("figma_sources"):
            ctx["figma_sources"] = self._doc_store.read("figma_sources")
            try:
                check_figma_mcp_available()
                ctx["figma_mcp_available"] = "true"
            except FigmaMCPNotConfigured:
                _event_log.warning(
                    "Figma MCP server not configured. UI agents will fall back "
                    "to text-based design references."
                )
                ctx["figma_mcp_available"] = "false"
        if self._doc_store.exists("figma_annotations"):
            ctx["figma_annotations"] = self._doc_store.read("figma_annotations")
        return ctx

    async def _run_track(
        self,
        sprint_number: int,
        sprint_scope: str,
        track: Track,
        api_contract: str,
        shared_context: dict[str, str],
        sprint_state: SprintState | None,
    ) -> QACycleResult | None:
        """Run the dev+QA cycle for a single track within a sprint."""
        progress: TrackProgress | None = None
        if sprint_state is not None:
            progress = sprint_state.track_progress.setdefault(
                track.name, TrackProgress(track_name=track.name),
            )
            if progress.phase == TrackPhase.COMPLETE:
                return None
            progress.phase = TrackPhase.DEV
            self._save_state()

        emit(_event_log, SprintPhaseEvent(
            sprint_number=sprint_number,
            sub_phase=f"{track.name}_dev",
            message=f"Sprint {sprint_number}: {track.name} development",
        ))

        feature_ids = extract_sprint_feature_ids(sprint_scope)
        track_spec = self._read_track_spec(track.name, feature_ids)

        input_docs: dict[str, str] = {
            "track_spec": track_spec,
            "api_contract": api_contract,
            "sprint_scope": sprint_scope,
            **shared_context,
        }
        extra_context = {
            "track_name": track.name,
            "track_kind": track.kind,
        }

        _stage_to_track_phase = {
            "action": TrackPhase.DEV,
            "initial_qa": TrackPhase.QA,
            "correction": TrackPhase.CORRECTION,
            "re_review": TrackPhase.QA,
        }

        def _on_stage(stage: str) -> None:
            if progress is not None and stage in _stage_to_track_phase:
                progress.phase = _stage_to_track_phase[stage]

        resume_session, resume_stage, resume_round = self._read_resume_cursor()

        result = await run_qa_cycle(
            claude=self._claude,
            action_agent=self._registry.get("developer"),
            qa_agent=self._registry.get("qa"),
            input_docs=input_docs,
            output_doc_name=f"sprint_{sprint_number}_{track.name}",
            workspace=self._project_dir / track.path,
            doc_store=self._doc_store,
            prompt_renderer=self._prompt_renderer,
            session_id=resume_session,
            resume_stage=resume_stage,
            resume_round=resume_round,
            on_progress=self._qa_cursor_writer(_on_stage),
            skip_action_output_in_qa=True,
            extra_context=extra_context,
            figma_mcp_enabled=shared_context.get("figma_mcp_available") == "true",
        )

        if progress is not None:
            progress.phase = TrackPhase.COMPLETE
        # Track passed: drop its cursor so the next track in the sprint starts
        # fresh (the in-flight track is always the first not-yet-complete one).
        self._clear_resume_cursor()
        self._save_state()
        return result

    async def _execute_sprint(
        self,
        sprint_number: int,
        sprint_scope: str,
        needs_integration: bool,
        partial_cost: list[float],
        sprint_state: SprintState | None = None,
    ) -> SprintResult:
        """Iterate tracks then run optional integration."""
        if sprint_state is not None and not sprint_state.tracks_in_scope:
            sprint_state.tracks_in_scope = [t.name for t in self._tracks]
            self._save_state()

        feature_ids = extract_sprint_feature_ids(sprint_scope)
        api_contract = (
            scope_spec_to_features(self._doc_store.read("api_contract"), feature_ids)
            if self._doc_store.exists("api_contract")
            else ""
        )
        shared_context = self._build_shared_context(sprint_state)

        track_results: dict[str, QACycleResult] = {}
        for track in self._tracks_for_sprint(sprint_state):
            result = await self._run_track(
                sprint_number, sprint_scope, track, api_contract,
                shared_context, sprint_state,
            )
            if result is not None:
                track_results[track.name] = result
                partial_cost[0] += result.total_cost

        integration_result = None
        if needs_integration and not (
            sprint_state is not None and sprint_state.status == SprintStatus.COMPLETE
        ):
            integration_result = await self._run_integration(
                sprint_number, sprint_scope, feature_ids,
                shared_context, sprint_state,
            )
            if integration_result is not None:
                partial_cost[0] += integration_result.total_cost

        if sprint_state is not None:
            sprint_state.status = SprintStatus.COMPLETE
            self._save_state()

        return SprintResult(
            sprint_number=sprint_number,
            success=True,
            total_cost=partial_cost[0],
            track_results=track_results,
            integration_result=integration_result,
        )

    async def _run_integration(
        self,
        sprint_number: int,
        sprint_scope: str,
        feature_ids: set[str],
        shared_context: dict[str, str],
        sprint_state: SprintState | None,
    ) -> QACycleResult | None:
        """Run the optional integration QA cycle for a sprint."""
        if sprint_state is not None:
            sprint_state.status = SprintStatus.INTEGRATION
            self._save_state()
        emit(_event_log, SprintPhaseEvent(
            sprint_number=sprint_number,
            sub_phase="integration",
            message=f"Sprint {sprint_number}: integration",
        ))

        spec_docs: dict[str, str] = {
            "sprint_scope": sprint_scope,
            **shared_context,
        }
        for track in self._tracks:
            doc_name = f"{track.name}_spec"
            if self._doc_store.exists(doc_name):
                spec_docs[doc_name] = scope_spec_to_features(
                    self._doc_store.read(doc_name), feature_ids,
                )
        if self._doc_store.exists("api_contract"):
            spec_docs["api_contract"] = scope_spec_to_features(
                self._doc_store.read("api_contract"), feature_ids,
            )

        services = sprint_state.integration_services if sprint_state else []
        mcp_config = self._resolve_integration_mcp_config(services)

        _stage_to_status = {
            "initial_qa": SprintStatus.INTEGRATION_QA,
            "correction": SprintStatus.INTEGRATION_CORRECTION,
            "re_review": SprintStatus.INTEGRATION_QA,
        }

        def _on_stage(stage: str) -> None:
            if sprint_state is not None and stage in _stage_to_status:
                sprint_state.status = _stage_to_status[stage]

        resume_session, resume_stage, resume_round = self._read_resume_cursor()

        result = await run_qa_cycle(
            claude=self._claude,
            action_agent=self._registry.get("integration"),
            qa_agent=self._registry.get("integration_qa"),
            input_docs=spec_docs,
            output_doc_name=f"sprint_{sprint_number}_integration",
            workspace=self._project_dir,
            doc_store=self._doc_store,
            prompt_renderer=self._prompt_renderer,
            qa_output_key="integration_guide",
            session_id=resume_session,
            resume_stage=resume_stage,
            resume_round=resume_round,
            on_progress=self._qa_cursor_writer(_on_stage),
            mcp_config=mcp_config,
        )
        # Integration passed: drop its cursor.
        self._clear_resume_cursor()
        self._save_state()
        return result
