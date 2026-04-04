"""Pipeline engine: main coordinator that ties all phases together."""

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path

from agentic_dev.agents.registry import AgentRegistry
from agentic_dev.claude.output_parser import OutputParser
from agentic_dev.claude.runner import ClaudeRunner
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import (
    AgentRunError,
    CheckpointPause,
    OutputParseError,
)
from agentic_dev.orchestrator.agent_bridge import to_run_config
from agentic_dev.orchestrator.checkpoint import CheckpointConfig, should_pause
from agentic_dev.orchestrator.qa_cycle import run_qa_cycle
from agentic_dev.orchestrator.sprint_runner import SprintRunner
from agentic_dev.prompts.renderer import PromptRenderer
from agentic_dev.state.manager import StateManager
from agentic_dev.state.models import (
    AgentRunRecord,
    PipelinePhase,
    PipelineState,
    ProjectType,
    SprintState,
    SprintStatus,
)
from agentic_dev.state.transitions import advance_phase
from agentic_dev.workspace.claude_md import (
    generate_backend_claude_md,
    generate_frontend_claude_md,
    parse_tech_stack,
    write_claude_md,
)
from agentic_dev.workspace.git import commit, has_changes, init_repo


class PipelineEngine:
    """Finite state machine that advances through pipeline phases."""

    def __init__(
        self,
        project_dir: Path,
        claude: ClaudeRunner,
        registry: AgentRegistry,
        doc_store: DocumentStore,
        prompt_renderer: PromptRenderer,
        state_manager: StateManager,
        checkpoint_config: CheckpointConfig,
    ) -> None:
        self._project_dir = project_dir
        self._claude = claude
        self._registry = registry
        self._doc_store = doc_store
        self._prompt_renderer = prompt_renderer
        self._state_manager = state_manager
        self._checkpoint_config = checkpoint_config
        self._output_parser = OutputParser()

    def _get_sprint_runner(self, project_type: str = "fullstack") -> SprintRunner:
        """Create a SprintRunner configured for the given project type."""
        return SprintRunner(
            claude=self._claude,
            registry=self._registry,
            doc_store=self._doc_store,
            prompt_renderer=self._prompt_renderer,
            project_dir=self._project_dir,
            project_type=project_type,
        )

    async def run(self) -> None:
        """Main loop: load state, execute current phase, advance, persist.

        Raises CheckpointPause when the pipeline should pause for human review.
        """
        state = self._state_manager.load()

        while state.phase not in (PipelinePhase.COMPLETE, PipelinePhase.FAILED):
            try:
                state = await self._execute_phase(state)
            except (AgentRunError, OutputParseError) as exc:
                state.failed_at_phase = state.phase
                state.phase = PipelinePhase.FAILED
                state.error = str(exc)
                self._state_manager.save(state)
                raise

            self._state_manager.save(state)

            if should_pause(state.phase, self._checkpoint_config):
                raise CheckpointPause(phase=state.phase)

    async def _execute_phase(self, state: PipelineState) -> PipelineState:
        """Dispatch to the appropriate handler based on the current phase."""
        handlers = {
            PipelinePhase.IDLE: self._run_input_processing,
            PipelinePhase.INPUT_PROCESSING: self._run_input_processing,
            PipelinePhase.FEATURE_ANALYSIS: self._run_feature_analysis,
            PipelinePhase.ARCHITECTURE: self._run_architecture,
            PipelinePhase.SPRINT_PLANNING: self._run_sprint_planning,
            PipelinePhase.DESIGN_CHECKPOINT: self._advance_past_checkpoint,
            PipelinePhase.SPRINTING: self._run_sprints,
            PipelinePhase.UAT: self._run_uat,
        }

        # QA sub-phases advance to the next main phase
        qa_advance_map = {
            PipelinePhase.FEATURE_ANALYSIS_QA: PipelinePhase.ARCHITECTURE,
            PipelinePhase.ARCHITECTURE_QA: PipelinePhase.SPRINT_PLANNING,
            PipelinePhase.SPRINT_PLANNING_QA: PipelinePhase.DESIGN_CHECKPOINT,
        }

        if state.phase in qa_advance_map:
            return advance_phase(state, qa_advance_map[state.phase])

        handler = handlers.get(state.phase)
        if handler is None:
            return state

        return await handler(state)

    async def _run_input_processing(self, state: PipelineState) -> PipelineState:
        """Run the input processor agent (no QA cycle)."""
        if state.phase == PipelinePhase.IDLE:
            state = advance_phase(state, PipelinePhase.INPUT_PROCESSING)

        output = await self._run_single_agent(
            agent_name="input_processor",
            input_docs={"user_input": self._doc_store.read("user_input")},
            output_doc_name="structured_input",
        )
        self._doc_store.write("structured_input", output)

        state.project_type = self._parse_project_type(output)

        # Create code directories based on detected project type
        if state.has_frontend:
            (self._project_dir / "frontend").mkdir(parents=True, exist_ok=True)
        if state.has_backend:
            (self._project_dir / "backend").mkdir(parents=True, exist_ok=True)

        return advance_phase(state, PipelinePhase.FEATURE_ANALYSIS)

    @staticmethod
    def _parse_project_type(structured_input: str) -> ProjectType:
        """Extract the project type from the structured input document."""
        match = re.search(
            r"##\s*Project\s+Type\s*\n\s*(fullstack|frontend_only|backend_only)",
            structured_input,
        )
        if match:
            return ProjectType(match.group(1))
        return ProjectType.FULLSTACK

    async def _run_feature_analysis(self, state: PipelineState) -> PipelineState:
        """Run feature_analyst + feature_analyst_qa via QA cycle."""
        structured_input = self._doc_store.read("structured_input")

        result = await run_qa_cycle(
            claude=self._claude,
            action_agent=self._registry.get("feature_analyst"),
            qa_agent=self._registry.get("feature_analyst_qa"),
            input_docs={"structured_input": structured_input},
            output_doc_name="features",
            workspace=self._project_dir,
            doc_store=self._doc_store,
            prompt_renderer=self._prompt_renderer,
        )

        total_cost = result.total_cost
        state.total_cost_usd += total_cost
        self._record_agent_run(state, "feature_analyst", total_cost)

        return advance_phase(state, PipelinePhase.FEATURE_ANALYSIS_QA)

    async def _run_architecture(self, state: PipelineState) -> PipelineState:
        """Run architect + architect_qa. Parse multi-document output."""
        features = self._doc_store.read("features")
        structured_input = self._doc_store.read("structured_input")
        design_analyses = (
            self._doc_store.read("design_analyses")
            if self._doc_store.exists("design_analyses")
            else ""
        )
        project_type_str = state.project_type.value if state.project_type else "fullstack"
        extra_context = {"project_type": project_type_str}

        result = await run_qa_cycle(
            claude=self._claude,
            action_agent=self._registry.get("architect"),
            qa_agent=self._registry.get("architect_qa"),
            input_docs={
                "structured_input": structured_input,
                "features": features,
                "design_analyses": design_analyses,
            },
            output_doc_name="architecture",
            workspace=self._project_dir,
            doc_store=self._doc_store,
            prompt_renderer=self._prompt_renderer,
            extra_context=extra_context,
        )

        # Split multi-document output into separate specs
        docs = self._output_parser.split_documents(
            result.output,
            expected_documents=state.expected_architecture_docs,
            agent_name="architect",
        )
        for doc_name, content in docs.items():
            self._doc_store.write(doc_name, content)

        total_cost = result.total_cost
        state.total_cost_usd += total_cost
        self._record_agent_run(state, "architect", total_cost)

        return advance_phase(state, PipelinePhase.ARCHITECTURE_QA)

    async def _run_sprint_planning(self, state: PipelineState) -> PipelineState:
        """Run sprint_planner + sprint_planner_qa. Parse sprint plan."""
        features = self._doc_store.read("features")
        input_docs: dict[str, str] = {"features": features}

        if state.has_frontend:
            input_docs["frontend_spec"] = self._doc_store.read("frontend_spec")
        else:
            input_docs["frontend_spec"] = ""

        if state.has_backend:
            input_docs["backend_spec"] = self._doc_store.read("backend_spec")
            input_docs["api_contract"] = self._doc_store.read("api_contract")
        else:
            input_docs["backend_spec"] = ""
            input_docs["api_contract"] = ""

        result = await run_qa_cycle(
            claude=self._claude,
            action_agent=self._registry.get("sprint_planner"),
            qa_agent=self._registry.get("sprint_planner_qa"),
            input_docs=input_docs,
            output_doc_name="sprint_plan",
            workspace=self._project_dir,
            doc_store=self._doc_store,
            prompt_renderer=self._prompt_renderer,
        )

        # Populate sprint states from the plan output
        sprint_docs = self._output_parser.split_documents(
            result.output,
            expected_documents=["sprint_plan"],
            agent_name="sprint_planner",
        )
        sprint_plan_text = sprint_docs.get("sprint_plan", result.output)
        state.sprints = self._parse_sprint_plan(sprint_plan_text)
        state.current_sprint = 1 if state.sprints else None

        total_cost = result.total_cost
        state.total_cost_usd += total_cost
        self._record_agent_run(state, "sprint_planner", total_cost)

        return advance_phase(state, PipelinePhase.SPRINT_PLANNING_QA)

    async def _advance_past_checkpoint(self, state: PipelineState) -> PipelineState:
        """Advance past a checkpoint to the sprinting phase.

        If checkpoint_feedback was provided, store it so sprint agents can
        reference it as additional context.
        """
        if state.checkpoint_feedback:
            self._doc_store.write("checkpoint_feedback", state.checkpoint_feedback)
            state.checkpoint_feedback = None

        await self._setup_workspaces(state)

        return advance_phase(state, PipelinePhase.SPRINTING)

    async def _setup_workspaces(self, state: PipelineState) -> None:
        """Initialize git repos and generate CLAUDE.md for code directories."""
        project_name = state.project_name

        if state.has_frontend:
            frontend_dir = self._project_dir / "frontend"
            tech_stack = self._read_tech_stack("frontend_spec")
            content = generate_frontend_claude_md(project_name, tech_stack)
            write_claude_md(frontend_dir, content)
            await init_repo(frontend_dir)
            await commit(frontend_dir, "Initial commit: project scaffold and CLAUDE.md")

        if state.has_backend:
            backend_dir = self._project_dir / "backend"
            tech_stack = self._read_tech_stack("backend_spec")
            content = generate_backend_claude_md(project_name, tech_stack)
            write_claude_md(backend_dir, content)
            await init_repo(backend_dir)
            await commit(backend_dir, "Initial commit: project scaffold and CLAUDE.md")

    async def _commit_sprint_changes(
        self, state: PipelineState, sprint: SprintState
    ) -> None:
        """Commit changes in code directories after a successful sprint."""
        message = f"Sprint {sprint.sprint_number}: {sprint.name}"
        dirs = []
        if state.has_frontend:
            dirs.append(self._project_dir / "frontend")
        if state.has_backend:
            dirs.append(self._project_dir / "backend")

        for code_dir in dirs:
            if await has_changes(code_dir):
                await commit(code_dir, message)

    def _read_tech_stack(self, doc_name: str) -> dict[str, str]:
        """Read a spec document and parse its tech stack, returning defaults on failure."""
        try:
            spec_text = self._doc_store.read(doc_name)
            return parse_tech_stack(spec_text)
        except Exception:
            return {}

    async def _run_sprints(self, state: PipelineState) -> PipelineState:
        """Run each sprint sequentially using SprintRunner."""
        project_type_str = state.project_type.value if state.project_type else "fullstack"
        sprint_runner = self._get_sprint_runner(project_type_str)

        for sprint in state.sprints:
            if sprint.status == SprintStatus.COMPLETE:
                continue

            sprint.status = SprintStatus.BACKEND_DEV
            sprint.started_at = datetime.now(timezone.utc)
            state.current_sprint = sprint.sprint_number

            sprint_scope = self._doc_store.read(
                f"sprint_{sprint.sprint_number}_scope"
            ) if self._doc_store.exists(
                f"sprint_{sprint.sprint_number}_scope"
            ) else sprint.name

            needs_integration = self._doc_store.exists(
                f"sprint_{sprint.sprint_number}_integration_flag"
            )

            result = await sprint_runner.run_sprint(
                sprint_number=sprint.sprint_number,
                sprint_scope=sprint_scope,
                needs_integration=needs_integration,
            )

            sprint.status = SprintStatus.COMPLETE if result.success else SprintStatus.FAILED
            sprint.completed_at = datetime.now(timezone.utc)
            state.total_cost_usd += result.total_cost

            if result.success:
                await self._commit_sprint_changes(state, sprint)

            self._state_manager.save(state)

            if should_pause(
                PipelinePhase.SPRINTING,
                self._checkpoint_config,
                sprint_just_completed=True,
            ):
                raise CheckpointPause(phase=PipelinePhase.SPRINTING)

            if not result.success:
                state.failed_at_phase = PipelinePhase.SPRINTING
                state.error = f"Sprint {sprint.sprint_number} failed"
                return advance_phase(state, PipelinePhase.FAILED)

        return advance_phase(state, PipelinePhase.UAT)

    async def _run_uat(self, state: PipelineState) -> PipelineState:
        """Run the UAT agent (no QA cycle)."""
        input_docs = {}
        for doc_name in ["features", "frontend_spec", "backend_spec", "api_contract", "sprint_plan"]:
            if self._doc_store.exists(doc_name):
                input_docs[doc_name] = self._doc_store.read(doc_name)

        output = await self._run_single_agent(
            agent_name="uat",
            input_docs=input_docs,
            output_doc_name="uat_report",
        )
        self._doc_store.write("uat_report", output)

        return advance_phase(state, PipelinePhase.COMPLETE)

    async def _run_single_agent(
        self,
        agent_name: str,
        input_docs: dict[str, str],
        output_doc_name: str,
    ) -> str:
        """Run a single agent without a QA cycle."""
        agent_def = self._registry.get(agent_name)
        prompt = self._prompt_renderer.render_agent_prompt(
            template_name=agent_def.prompt_template,
            input_documents=input_docs,
            constraints=agent_def.constraints,
        )
        config = to_run_config(agent_def)
        result = await self._claude.run(
            agent=config,
            prompt=prompt,
            working_dir=self._project_dir,
        )

        if not result.text.strip():
            await asyncio.sleep(5.0)
            result = await self._claude.run(
                agent=config,
                prompt=prompt,
                working_dir=self._project_dir,
            )

        if not result.text.strip():
            raise AgentRunError(
                agent_name=agent_name,
                message="Agent returned empty output",
            )

        return result.text

    def _record_agent_run(
        self,
        state: PipelineState,
        agent_name: str,
        cost: float,
    ) -> None:
        """Add an AgentRunRecord to the pipeline state."""
        state.agent_runs.append(
            AgentRunRecord(
                agent_name=agent_name,
                phase=state.phase,
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
                cost_usd=cost,
                success=True,
            )
        )

    @staticmethod
    def _parse_sprint_plan(plan_text: str) -> list[SprintState]:
        """Extract sprint entries from the plan text.

        Looks for lines matching "Sprint N: <name>" pattern.
        Falls back to a single sprint if no pattern is found.
        """
        import re

        sprints: list[SprintState] = []
        for match in re.finditer(r"Sprint\s+(\d+):\s*(.+)", plan_text):
            number = int(match.group(1))
            name = match.group(2).strip()
            sprints.append(SprintState(sprint_number=number, name=name))

        if not sprints:
            sprints.append(SprintState(sprint_number=1, name="Sprint 1"))

        return sprints
