"""Sync orchestrator: detects drift between code, specs, and Figma designs.

Runs code_analyzer agents to snapshot current state, then drift_detector
to compare against specs, producing a SyncReport. Resolution application
uses spec_updater for to_spec items and generates change requests for to_code items.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from agentic_dev.agents.registry import AgentRegistry
from agentic_dev.claude.llm_parser import parse_with_llm
from agentic_dev.claude.runner import ClaudeRunner
from agentic_dev.config import DirectoryMap
from agentic_dev.documents.store import DocumentStore
from agentic_dev.logging import get_event_logger, emit
from agentic_dev.logging.events import (
    DriftDetectionEvent,
    SyncResolutionEvent,
    SyncStartEvent,
)
from agentic_dev.orchestrator.qa_cycle import run_qa_cycle
from agentic_dev.prompts.renderer import PromptRenderer
from agentic_dev.state.models import DriftItem, SyncReport
from agentic_dev.state.parser_models import ParsedDriftReport

_event_log = get_event_logger("sync")


@dataclass
class SyncApplyResult:
    """Result of applying sync resolutions."""

    specs_updated: int = 0
    code_changes_queued: int = 0
    items_ignored: int = 0
    items_deferred: int = 0
    total_cost: float = 0.0


async def run_sync(
    claude: ClaudeRunner,
    registry: AgentRegistry,
    prompt_renderer: PromptRenderer,
    doc_store: DocumentStore,
    project_dir: Path,
    directory_map: DirectoryMap,
    scope: Literal["all", "api", "frontend", "backend"] = "all",
    sync_ignores: list[str] | None = None,
) -> SyncReport:
    """Run drift detection between code and specs.

    Args:
        claude: The ClaudeRunner instance.
        registry: Agent registry.
        prompt_renderer: Prompt renderer.
        doc_store: Document store for the project.
        project_dir: Root path of the project.
        directory_map: Mapping of frontend/backend directories.
        scope: Which area to check ("all", "api", "frontend", "backend").
        sync_ignores: Drift item IDs to exclude from the report.

    Returns:
        SyncReport with all detected drift items.
    """
    emit(_event_log, SyncStartEvent(
        scope=scope,
        message=f"Sync started: scope={scope}",
    ))

    # Step 1: Run code_analyzer agents with QA in parallel
    snapshots = await _analyze_code(
        claude=claude,
        registry=registry,
        prompt_renderer=prompt_renderer,
        doc_store=doc_store,
        project_dir=project_dir,
        directory_map=directory_map,
        scope=scope,
    )

    # Step 2: Collect current spec documents
    spec_documents = _collect_specs(doc_store)

    # Step 2b: Collect design context
    figma_sources, figma_mcp_available = _collect_design_context(doc_store)

    # Step 3: Run drift_detector with QA
    drift_report = await _detect_drift(
        claude=claude,
        registry=registry,
        prompt_renderer=prompt_renderer,
        doc_store=doc_store,
        project_dir=project_dir,
        code_snapshots=snapshots,
        spec_documents=spec_documents,
        sync_ignores=sync_ignores or [],
        figma_sources=figma_sources,
        figma_mcp_available=figma_mcp_available,
    )

    emit(_event_log, DriftDetectionEvent(
        drift_items_found=len(drift_report.items),
        summary=drift_report.summary,
        message=f"Drift detection: {len(drift_report.items)} items found",
    ))

    return drift_report


async def apply_sync_resolutions(
    claude: ClaudeRunner,
    registry: AgentRegistry,
    prompt_renderer: PromptRenderer,
    doc_store: DocumentStore,
    project_dir: Path,
    report: SyncReport,
) -> SyncApplyResult:
    """Apply resolved drift items.

    - to_spec items: run spec_updater to update spec documents
    - to_code items: compose sync_change_request.md
    - ignore items: returned for caller to save to config
    - defer items: no action

    Args:
        claude: The ClaudeRunner instance.
        registry: Agent registry.
        prompt_renderer: Prompt renderer.
        doc_store: Document store.
        project_dir: Project root path.
        report: SyncReport with resolutions set on each item.

    Returns:
        SyncApplyResult with counts of actions taken.
    """
    result = SyncApplyResult()

    to_spec_items = [i for i in report.items if i.resolution == "to_spec"]
    to_code_items = [i for i in report.items if i.resolution == "to_code"]
    ignore_items = [i for i in report.items if i.resolution == "ignore"]
    defer_items = [i for i in report.items if i.resolution == "defer"]

    # Apply to_spec resolutions by broadcasting to all existing specs.
    # Each spec_updater agent receives all items and incorporates only
    # those relevant to its spec, eliminating scope-based misrouting.
    if to_spec_items:
        for spec_name in ("frontend_spec", "backend_spec", "api_contract"):
            if not doc_store.exists(spec_name):
                continue
            cost = await _update_spec(
                claude=claude,
                registry=registry,
                prompt_renderer=prompt_renderer,
                doc_store=doc_store,
                project_dir=project_dir,
                spec_name=spec_name,
                resolved_items=to_spec_items,
            )
            result.total_cost += cost
            result.specs_updated += 1

    # Compose change request for to_code items
    if to_code_items:
        change_request = _compose_change_request(to_code_items)
        doc_store.write("sync_change_request", change_request)
        result.code_changes_queued = len(to_code_items)

    result.items_ignored = len(ignore_items)
    result.items_deferred = len(defer_items)

    emit(_event_log, SyncResolutionEvent(
        specs_updated=result.specs_updated,
        code_changes_queued=result.code_changes_queued,
        items_ignored=result.items_ignored,
        items_deferred=result.items_deferred,
        total_cost=result.total_cost,
        message=(
            f"Sync resolutions applied: {result.specs_updated} specs updated, "
            f"{result.code_changes_queued} code changes queued"
        ),
    ))

    return result


async def _analyze_code(
    claude: ClaudeRunner,
    registry: AgentRegistry,
    prompt_renderer: PromptRenderer,
    doc_store: DocumentStore,
    project_dir: Path,
    directory_map: DirectoryMap,
    scope: str,
) -> str:
    """Run code_analyzer agents with QA cycles and return combined snapshots."""
    action_agent = registry.get("code_analyzer")
    qa_agent = registry.get("code_analyzer_qa")
    tasks = []

    scopes_to_analyze = []
    if scope in ("all", "backend", "api") and directory_map.backend:
        scopes_to_analyze.append(("backend", directory_map.backend))
    if scope in ("all", "frontend") and directory_map.frontend:
        scopes_to_analyze.append(("frontend", directory_map.frontend))

    for analysis_scope, dir_name in scopes_to_analyze:
        tasks.append(
            run_qa_cycle(
                claude=claude,
                action_agent=action_agent,
                qa_agent=qa_agent,
                input_docs={},
                output_doc_name=f"code_snapshot_{analysis_scope}",
                workspace=project_dir / dir_name,
                doc_store=doc_store,
                prompt_renderer=prompt_renderer,
                qa_output_key="code_snapshot",
                extra_context={"analysis_scope": analysis_scope},
            )
        )

    if not tasks:
        return ""

    results = await asyncio.gather(*tasks)
    return "\n\n---\n\n".join(r.output for r in results)


def _collect_specs(doc_store: DocumentStore) -> str:
    """Read all spec documents and combine them."""
    parts = []
    for doc_name in ("frontend_spec", "backend_spec", "api_contract"):
        if doc_store.exists(doc_name):
            parts.append(f"## {doc_name}\n\n{doc_store.read(doc_name)}")
    return "\n\n---\n\n".join(parts)


def _collect_design_context(doc_store: DocumentStore) -> tuple[str, str]:
    """Read figma_sources and check MCP availability.

    Returns:
        A tuple of (figma_sources, figma_mcp_available) strings.
        Empty string and ``"false"`` if the documents do not exist.
    """
    figma_sources = ""
    figma_mcp_available = "false"
    if doc_store.exists("figma_sources"):
        figma_sources = doc_store.read("figma_sources")
        try:
            from agentic_dev.onboarding.figma import check_figma_mcp_available, FigmaMCPNotConfigured  # noqa: WPS433

            check_figma_mcp_available()
            figma_mcp_available = "true"
        except FigmaMCPNotConfigured:
            pass
        except Exception as exc:  # noqa: BLE001
            _log = get_event_logger("sync")
            _log.warning(
                "Figma MCP check failed unexpectedly: %s. "
                "Design drift detection may be incomplete.",
                exc,
            )
    return figma_sources, figma_mcp_available


async def _detect_drift(
    claude: ClaudeRunner,
    registry: AgentRegistry,
    prompt_renderer: PromptRenderer,
    doc_store: DocumentStore,
    project_dir: Path,
    code_snapshots: str,
    spec_documents: str,
    sync_ignores: list[str],
    figma_sources: str = "",
    figma_mcp_available: str = "false",
) -> SyncReport:
    """Run drift_detector agent with QA cycle and parse the output into a SyncReport."""
    action_agent = registry.get("drift_detector")
    qa_agent = registry.get("drift_detector_qa")

    result = await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={},
        output_doc_name="drift_report",
        workspace=project_dir,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        qa_output_key="drift_report",
        extra_context={
            "code_snapshots": code_snapshots,
            "spec_documents": spec_documents,
            "figma_sources": figma_sources,
            "figma_mcp_available": figma_mcp_available,
            "sync_ignores": sync_ignores,
        },
    )

    return await _parse_drift_report(
        claude=claude,
        working_dir=project_dir,
        text=result.output,
    )


_DRIFT_ITEM_LINE_RE = re.compile(r"^\s*-\s*\[DRIFT-", re.MULTILINE)


async def _parse_drift_report(
    *,
    claude: ClaudeRunner,
    working_dir: Path,
    text: str,
) -> SyncReport:
    """Parse drift detector output into a structured SyncReport via LLM parser.

    The drift report is prose where each item's description can include
    phrases like "found in" mid-sentence; line-based ``rsplit`` was unsafe.
    The LLM parser handles that; a regex count of ``- [DRIFT-`` lines acts
    as a sanity check on the LLM's item count.

    An empty drift report (no ``- [DRIFT-`` items) short-circuits to an
    empty SyncReport without calling the LLM.
    """
    expected_count = len(_DRIFT_ITEM_LINE_RE.findall(text))
    if expected_count == 0:
        return SyncReport(
            generated_at=datetime.now(timezone.utc),
            items=[],
            summary="0 drift item(s) found",
        )

    def sanity_check(parsed: ParsedDriftReport) -> None:
        if len(parsed.items) != expected_count:
            raise ValueError(
                f"drift item count mismatch: input has {expected_count} "
                f"'- [DRIFT-' lines, LLM returned {len(parsed.items)}",
            )

    extraction_prompt = (
        "Extract every drift item listed in the drift report below.\n\n"
        "Each drift item is a list line of the form `- [DRIFT-NNN] "
        "<description>`, possibly with a trailing source/spec reference. "
        "For every such line, populate one item with:\n"
        "- id: the bracketed identifier including the brackets, exactly as "
        "  written (e.g. \"[DRIFT-001]\")\n"
        "- scope: derived from the most recent `## ` heading above the item — "
        "  one of \"api\" (when heading mentions API/contract), "
        "  \"frontend\", \"backend\", or \"figma\"\n"
        "- category: derived from the most recent `### ` heading above the "
        "  item — one of \"in_code_not_spec\" (heading mentions \"in code "
        "  but not in spec\"), \"in_spec_not_code\" (\"in spec but not in "
        "  code\"), \"difference\" (\"differences\"), or \"design_drift\" "
        "  (\"design token drift\")\n"
        "- description: the natural-language description, with any trailing "
        "  \"found in <path>\" / \"specified in <path>\" suffix removed\n"
        "- source_file: the path after \"found in\" if present at the END "
        "  of the line (not mid-sentence), else null\n"
        "- spec_reference: the path after \"specified in\" if present at "
        "  the END of the line, else null\n"
        "\n"
        "Also extract a single `summary` string from the `## Summary` "
        "section if one exists; otherwise leave it empty. Do not invent "
        "items that aren't in a `- [DRIFT-` list line."
    )

    parsed = await parse_with_llm(
        claude=claude,
        text=text,
        schema_model=ParsedDriftReport,
        extraction_prompt=extraction_prompt,
        working_dir=working_dir,
        sanity_check=sanity_check,
        agent_name="drift_report_parser",
    )

    items = [
        DriftItem(
            id=entry.id,
            scope=entry.scope,
            category=entry.category,
            description=entry.description,
            source_file=entry.source_file,
            spec_reference=entry.spec_reference,
        )
        for entry in parsed.items
    ]

    return SyncReport(
        generated_at=datetime.now(timezone.utc),
        items=items,
        summary=parsed.summary or f"{len(items)} drift item(s) found",
    )


async def _update_spec(
    claude: ClaudeRunner,
    registry: AgentRegistry,
    prompt_renderer: PromptRenderer,
    doc_store: DocumentStore,
    project_dir: Path,
    spec_name: str,
    resolved_items: list[DriftItem],
) -> float:
    """Run spec_updater with QA cycle to surgically update a spec document."""
    action_agent = registry.get("spec_updater")
    qa_agent = registry.get("spec_updater_qa")

    current_spec = doc_store.read(spec_name)
    items_data = [
        {"id": item.id, "category": item.category, "description": item.description}
        for item in resolved_items
    ]

    result = await run_qa_cycle(
        claude=claude,
        action_agent=action_agent,
        qa_agent=qa_agent,
        input_docs={},
        output_doc_name=spec_name,
        workspace=project_dir,
        doc_store=doc_store,
        prompt_renderer=prompt_renderer,
        qa_output_key="updated_spec",
        extra_context={
            "original_spec": current_spec,
            "resolved_items": items_data,
        },
    )

    if result.output.strip():
        doc_store.write(spec_name, result.output)

    return result.total_cost


def _compose_change_request(items: list[DriftItem]) -> str:
    """Compose a change request document from to_code drift items."""
    lines = [
        "# Sync Change Request",
        "",
        "The following items from the sync report need code changes to match specs:",
        "",
    ]
    for item in items:
        lines.append(f"## {item.id}: {item.description}")
        lines.append(f"- **Scope:** {item.scope}")
        lines.append(f"- **Category:** {item.category}")
        if item.spec_reference:
            lines.append(f"- **Spec reference:** {item.spec_reference}")
        lines.append("")

    return "\n".join(lines)
