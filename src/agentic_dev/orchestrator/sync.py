"""Sync orchestrator: detects drift between code, specs, and Figma designs.

Runs code_analyzer agents to snapshot current state, then drift_detector
to compare against specs, producing a SyncReport. Resolution application
uses spec_updater for to_spec items and generates change requests for to_code items.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from agentic_dev.agents.registry import AgentRegistry
from agentic_dev.claude.runner import ClaudeRunner
from agentic_dev.config import DirectoryMap
from agentic_dev.documents.store import DocumentStore
from agentic_dev.orchestrator.agent_bridge import to_run_config
from agentic_dev.prompts.renderer import PromptRenderer
from agentic_dev.state.models import DriftItem, SyncReport


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
    # Step 1: Run code_analyzer agents in parallel
    snapshots = await _analyze_code(
        claude=claude,
        registry=registry,
        prompt_renderer=prompt_renderer,
        project_dir=project_dir,
        directory_map=directory_map,
        scope=scope,
    )

    # Step 2: Collect current spec documents
    spec_documents = _collect_specs(doc_store)

    # Step 3: Run drift_detector
    drift_report = await _detect_drift(
        claude=claude,
        registry=registry,
        prompt_renderer=prompt_renderer,
        project_dir=project_dir,
        code_snapshots=snapshots,
        spec_documents=spec_documents,
        sync_ignores=sync_ignores or [],
    )

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

    # Apply to_spec resolutions using spec_updater
    if to_spec_items:
        specs_to_update = _group_items_by_spec(to_spec_items)
        for spec_name, items in specs_to_update.items():
            if not doc_store.exists(spec_name):
                continue
            cost = await _update_spec(
                claude=claude,
                registry=registry,
                prompt_renderer=prompt_renderer,
                doc_store=doc_store,
                project_dir=project_dir,
                spec_name=spec_name,
                resolved_items=items,
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

    return result


async def _analyze_code(
    claude: ClaudeRunner,
    registry: AgentRegistry,
    prompt_renderer: PromptRenderer,
    project_dir: Path,
    directory_map: DirectoryMap,
    scope: str,
) -> str:
    """Run code_analyzer agents and return combined snapshots."""
    agent_def = registry.get("code_analyzer")
    config = to_run_config(agent_def)
    tasks = []

    scopes_to_analyze = []
    if scope in ("all", "backend", "api") and directory_map.backend:
        scopes_to_analyze.append(("backend", directory_map.backend))
    if scope in ("all", "frontend") and directory_map.frontend:
        scopes_to_analyze.append(("frontend", directory_map.frontend))

    for analysis_scope, dir_name in scopes_to_analyze:
        prompt = prompt_renderer.render(
            agent_def.prompt_template,
            {"analysis_scope": analysis_scope},
        )
        tasks.append(
            claude.run(
                agent=config,
                prompt=prompt,
                working_dir=project_dir / dir_name,
            )
        )

    if not tasks:
        return ""

    results = await asyncio.gather(*tasks)
    return "\n\n---\n\n".join(r.text for r in results)


def _collect_specs(doc_store: DocumentStore) -> str:
    """Read all spec documents and combine them."""
    parts = []
    for doc_name in ("frontend_spec", "backend_spec", "api_contract"):
        if doc_store.exists(doc_name):
            parts.append(f"## {doc_name}\n\n{doc_store.read(doc_name)}")
    return "\n\n---\n\n".join(parts)


async def _detect_drift(
    claude: ClaudeRunner,
    registry: AgentRegistry,
    prompt_renderer: PromptRenderer,
    project_dir: Path,
    code_snapshots: str,
    spec_documents: str,
    sync_ignores: list[str],
) -> SyncReport:
    """Run drift_detector agent and parse the output into a SyncReport."""
    agent_def = registry.get("drift_detector")
    config = to_run_config(agent_def)

    prompt = prompt_renderer.render(
        agent_def.prompt_template,
        {
            "code_snapshots": code_snapshots,
            "spec_documents": spec_documents,
            "figma_analysis": "",
            "sync_ignores": sync_ignores,
        },
    )

    result = await claude.run(
        agent=config,
        prompt=prompt,
        working_dir=project_dir,
    )

    return _parse_drift_report(result.text)


def _parse_drift_report(text: str) -> SyncReport:
    """Parse drift detector output into a structured SyncReport."""
    items: list[DriftItem] = []
    current_scope: str = "api"
    current_category: str = "difference"

    scope_map = {
        "api contract": "api",
        "frontend": "frontend",
        "backend": "backend",
        "figma": "figma",
    }
    category_map = {
        "in code but not in spec": "in_code_not_spec",
        "in spec but not in code": "in_spec_not_code",
        "differences": "difference",
        "design token drift": "design_drift",
    }

    for line in text.splitlines():
        stripped = line.strip()

        # Detect scope headers (## API Contract, ## Frontend, etc.)
        if stripped.startswith("## "):
            header = stripped[3:].strip().lower()
            for key, scope_val in scope_map.items():
                if key in header:
                    current_scope = scope_val
                    break

        # Detect category headers (### In code but not in spec, etc.)
        if stripped.startswith("### "):
            header = stripped[4:].strip().lower()
            for key, cat_val in category_map.items():
                if key in header:
                    current_category = cat_val
                    break

        # Parse drift items (- [DRIFT-001] description)
        if stripped.startswith("- [DRIFT-"):
            bracket_end = stripped.find("]", 3)
            if bracket_end == -1:
                continue
            drift_id = stripped[2:bracket_end + 1]
            description = stripped[bracket_end + 1:].strip().lstrip("— ").lstrip("- ")

            source_file = None
            spec_reference = None
            if "found in " in description:
                parts = description.rsplit("found in ", 1)
                description = parts[0].rstrip(" —-")
                source_file = parts[1].strip()
            elif "specified in " in description:
                parts = description.rsplit("specified in ", 1)
                description = parts[0].rstrip(" —-")
                spec_reference = parts[1].strip()

            items.append(DriftItem(
                id=drift_id,
                scope=current_scope,
                category=current_category,
                description=description,
                source_file=source_file,
                spec_reference=spec_reference,
            ))

    summary_line = ""
    for line in text.splitlines():
        if line.strip().lower().startswith("## summary"):
            idx = text.splitlines().index(line)
            remaining = text.splitlines()[idx + 1:]
            for sline in remaining:
                if sline.strip() and not sline.strip().startswith("#"):
                    summary_line = sline.strip()
                    break
            break

    return SyncReport(
        generated_at=datetime.now(timezone.utc),
        items=items,
        summary=summary_line or f"{len(items)} drift item(s) found",
    )


def _group_items_by_spec(items: list[DriftItem]) -> dict[str, list[DriftItem]]:
    """Group drift items by which spec document they affect."""
    groups: dict[str, list[DriftItem]] = {}
    scope_to_spec = {
        "api": "api_contract",
        "frontend": "frontend_spec",
        "backend": "backend_spec",
    }
    for item in items:
        spec_name = scope_to_spec.get(item.scope, "api_contract")
        groups.setdefault(spec_name, []).append(item)
    return groups


async def _update_spec(
    claude: ClaudeRunner,
    registry: AgentRegistry,
    prompt_renderer: PromptRenderer,
    doc_store: DocumentStore,
    project_dir: Path,
    spec_name: str,
    resolved_items: list[DriftItem],
) -> float:
    """Run spec_updater to surgically update a spec document."""
    agent_def = registry.get("spec_updater")
    config = to_run_config(agent_def)

    current_spec = doc_store.read(spec_name)
    items_data = [
        {"id": item.id, "category": item.category, "description": item.description}
        for item in resolved_items
    ]

    prompt = prompt_renderer.render(
        agent_def.prompt_template,
        {
            "spec_document": current_spec,
            "resolved_items": items_data,
            "constraints": agent_def.constraints,
        },
    )

    result = await claude.run(
        agent=config,
        prompt=prompt,
        working_dir=project_dir,
    )

    if result.text.strip():
        doc_store.write(spec_name, result.text)

    return result.cost_usd


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
