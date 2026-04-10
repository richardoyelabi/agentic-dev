"""Adoption orchestrator: reverse-engineers full specs from existing codebases.

Runs spec_reverse_engineer agents (with QA cycles) for frontend_spec,
backend_spec, and api_contract, then extracts features.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from agentic_dev.agents.registry import AgentRegistry
from agentic_dev.claude.runner import ClaudeRunner
from agentic_dev.config import DirectoryMap
from agentic_dev.documents.store import DocumentStore
from agentic_dev.logging import get_event_logger, emit
from agentic_dev.logging.events import (
    AdoptionCompleteEvent,
    AdoptionStartEvent,
    SpecReverseEngineerEvent,
)
from agentic_dev.orchestrator.qa_cycle import QACycleResult, run_qa_cycle
from agentic_dev.workspace.git import commit, has_changes, init_repo
from agentic_dev.prompts.renderer import PromptRenderer
from agentic_dev.state.models import ProjectType

_event_log = get_event_logger("adoption")

_SPEC_CONTENT_MARKERS: dict[str, list[str]] = {
    "frontend_spec": ["# Frontend Spec"],
    "backend_spec": ["# Backend Spec"],
    "api_contract": ["# API Contract"],
}


@dataclass
class AdoptionResult:
    """Result of running the adoption pipeline."""

    total_cost: float = 0.0
    documents_produced: list[str] = field(default_factory=list)
    features_count: int = 0
    endpoints_count: int = 0


async def run_adoption(
    claude: ClaudeRunner,
    registry: AgentRegistry,
    prompt_renderer: PromptRenderer,
    doc_store: DocumentStore,
    project_dir: Path,
    directory_map: DirectoryMap,
    project_type: ProjectType,
    design_analyses: str = "",
) -> AdoptionResult:
    """Run the full adoption pipeline to reverse-engineer specs from code.

    Executes spec_reverse_engineer agents with QA cycles, then extracts
    features. All specs are saved to the document store.

    Args:
        claude: The ClaudeRunner instance.
        registry: Agent registry with all agent definitions loaded.
        prompt_renderer: Prompt renderer for Jinja2 templates.
        doc_store: Document store for the adopted project.
        project_dir: Root path of the project being adopted.
        directory_map: Mapping of frontend/backend directories.
        project_type: Detected project type.
        design_analyses: Optional Figma design analyses to incorporate.

    Returns:
        AdoptionResult with cost and document stats.
    """
    result = AdoptionResult()

    emit(_event_log, AdoptionStartEvent(
        project_path=str(project_dir),
        project_type=project_type.value,
        message=f"Adoption starting: {project_type.value} project at {project_dir}",
    ))

    # Step 1: Reverse-engineer frontend and backend specs in parallel
    tasks = []
    if project_type in (ProjectType.FULLSTACK, ProjectType.FRONTEND_ONLY):
        tasks.append(
            _reverse_engineer_spec(
                claude=claude,
                registry=registry,
                prompt_renderer=prompt_renderer,
                doc_store=doc_store,
                workspace=project_dir / (directory_map.frontend or "frontend"),
                target_spec_type="frontend_spec",
                existing_specs="",
            )
        )
    if project_type in (ProjectType.FULLSTACK, ProjectType.BACKEND_ONLY):
        tasks.append(
            _reverse_engineer_spec(
                claude=claude,
                registry=registry,
                prompt_renderer=prompt_renderer,
                doc_store=doc_store,
                workspace=project_dir / (directory_map.backend or "backend"),
                target_spec_type="backend_spec",
                existing_specs="",
            )
        )

    spec_results = await asyncio.gather(*tasks)
    for qa_result, doc_name in spec_results:
        result.total_cost += qa_result.total_cost
        result.documents_produced.append(doc_name)
        emit(_event_log, SpecReverseEngineerEvent(
            spec_type=doc_name,
            total_cost=qa_result.total_cost,
            corrected=qa_result.corrected,
            message=f"Reverse-engineered {doc_name} (${qa_result.total_cost:.4f})",
        ))

    # Step 2: Reverse-engineer API contract (needs generated specs as context)
    if project_type in (ProjectType.FULLSTACK, ProjectType.BACKEND_ONLY):
        existing_specs_parts = []
        for doc_name in ("frontend_spec", "backend_spec"):
            if doc_store.exists(doc_name):
                existing_specs_parts.append(doc_store.read(doc_name))
        existing_specs = "\n\n---\n\n".join(existing_specs_parts)

        api_result, _ = await _reverse_engineer_spec(
            claude=claude,
            registry=registry,
            prompt_renderer=prompt_renderer,
            doc_store=doc_store,
            workspace=project_dir / (directory_map.backend or "backend"),
            target_spec_type="api_contract",
            existing_specs=existing_specs,
        )
        result.total_cost += api_result.total_cost
        result.documents_produced.append("api_contract")
        emit(_event_log, SpecReverseEngineerEvent(
            spec_type="api_contract",
            total_cost=api_result.total_cost,
            corrected=api_result.corrected,
            message=f"Reverse-engineered api_contract (${api_result.total_cost:.4f})",
        ))

    # Step 3: Extract features from all generated specs
    features_result = await _extract_features(
        claude=claude,
        registry=registry,
        prompt_renderer=prompt_renderer,
        doc_store=doc_store,
        workspace=project_dir,
    )
    result.total_cost += features_result.total_cost
    result.documents_produced.append("features")

    # Count features and endpoints for the summary
    if doc_store.exists("features"):
        features_text = doc_store.read("features")
        result.features_count = features_text.count("[EXISTING-F")
    if doc_store.exists("api_contract"):
        api_text = doc_store.read("api_contract")
        result.endpoints_count = api_text.count("[E0")

    # Step 4: Generate structured_input summary
    structured_input = _build_structured_input(doc_store, project_type)
    doc_store.write("structured_input", structured_input)
    result.documents_produced.append("structured_input")

    # Save design analyses if provided
    if design_analyses:
        doc_store.write("design_analyses", design_analyses)

    docs_dir = doc_store.docs_dir
    if docs_dir.is_dir():
        if not (docs_dir / ".git").is_dir():
            await init_repo(docs_dir)
        if await has_changes(docs_dir):
            await commit(docs_dir, "docs: adoption — reverse-engineered specs")

    emit(_event_log, AdoptionCompleteEvent(
        total_cost_usd=result.total_cost,
        documents_produced=result.documents_produced,
        features_count=result.features_count,
        endpoints_count=result.endpoints_count,
        message=(
            f"Adoption complete: {len(result.documents_produced)} docs, "
            f"{result.features_count} features, ${result.total_cost:.4f}"
        ),
    ))

    return result


async def _reverse_engineer_spec(
    claude: ClaudeRunner,
    registry: AgentRegistry,
    prompt_renderer: PromptRenderer,
    doc_store: DocumentStore,
    workspace: Path,
    target_spec_type: str,
    existing_specs: str,
) -> tuple[QACycleResult, str]:
    """Run spec_reverse_engineer + QA cycle for a single spec type."""
    action_agent = registry.get("spec_reverse_engineer")
    qa_agent = registry.get("spec_reverse_engineer_qa")

    qa_result = await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={},
        output_doc_name=target_spec_type,
        workspace=workspace,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        qa_output_key="spec_output",
        extra_context={
            "target_spec_type": target_spec_type,
            "existing_specs": existing_specs,
        },
        content_markers=_SPEC_CONTENT_MARKERS.get(target_spec_type),
    )

    return qa_result, target_spec_type


async def _extract_features(
    claude: ClaudeRunner,
    registry: AgentRegistry,
    prompt_renderer: PromptRenderer,
    doc_store: DocumentStore,
    workspace: Path,
) -> QACycleResult:
    """Run feature_extractor + QA cycle to extract features from specs."""
    action_agent = registry.get("feature_extractor")
    qa_agent = registry.get("feature_extractor_qa")

    # Collect all generated specs as input
    specs_parts = []
    for doc_name in ("frontend_spec", "backend_spec", "api_contract"):
        if doc_store.exists(doc_name):
            specs_parts.append(f"## {doc_name}\n\n{doc_store.read(doc_name)}")
    specs = "\n\n---\n\n".join(specs_parts)

    return await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={},
        output_doc_name="features",
        workspace=workspace,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        qa_output_key="features_output",
        extra_context={"specs": specs},
    )


def _build_structured_input(
    doc_store: DocumentStore,
    project_type: ProjectType,
) -> str:
    """Build a structured_input document summarizing the adopted project."""
    lines = [
        "# Structured Input",
        "",
        "## Project Type",
        project_type.value,
        "",
        "## Feature Requirements",
    ]

    if doc_store.exists("features"):
        features = doc_store.read("features")
        for line in features.splitlines():
            stripped = line.strip()
            if "[EXISTING-F" in stripped and stripped.startswith("## Feature:"):
                feature_ref = stripped.removeprefix("## Feature:").strip()
                lines.append(f"- {feature_ref}")

    lines.extend([
        "",
        "## Preferences",
        "### Tech Stack",
    ])

    for doc_name in ("frontend_spec", "backend_spec"):
        if doc_store.exists(doc_name):
            spec = doc_store.read(doc_name)
            for line in spec.splitlines():
                stripped = line.strip()
                if stripped.startswith("- Framework:") or stripped.startswith("- Database:"):
                    lines.append(stripped)

    return "\n".join(lines) + "\n"
