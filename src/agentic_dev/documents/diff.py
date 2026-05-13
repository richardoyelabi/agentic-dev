"""Document diffing for detecting changes between structured input versions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from agentic_dev.claude.runner import ClaudeRunner


class DiffResult(BaseModel):
    """Result of comparing two versions of a structured input document."""

    added_features: list[str]
    modified_features: list[str]
    removed_features: list[str]
    restart_from: str


def _extract_feature_ids(text: str) -> dict[str, str]:
    """Extract feature IDs and their full line content from a document.

    Looks for patterns like [F001], [F002], etc. and maps each ID
    to the line it appears on for content comparison.
    """
    features: dict[str, str] = {}
    for line in text.splitlines():
        match = re.search(r"\[(F\d+)\]", line)
        if match:
            feature_id = match.group(1)
            features[feature_id] = line.strip()
    return features


def diff_structured_input(old: str, new: str) -> DiffResult:
    """Compare old vs new structured input to determine what changed.

    Extracts feature IDs [FXXX] from both versions, then categorises
    each feature as added, modified, or removed. Determines the
    appropriate pipeline restart phase based on the scope of changes.
    """
    old_features = _extract_feature_ids(old)
    new_features = _extract_feature_ids(new)

    old_ids = set(old_features.keys())
    new_ids = set(new_features.keys())

    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)

    modified = sorted(
        fid
        for fid in old_ids & new_ids
        if old_features[fid] != new_features[fid]
    )

    restart_from = determine_restart_phase(
        DiffResult(
            added_features=added,
            modified_features=modified,
            removed_features=removed,
            restart_from="",
        ),
        content_changed=(old.strip() != new.strip()),
    )

    return DiffResult(
        added_features=added,
        modified_features=modified,
        removed_features=removed,
        restart_from=restart_from,
    )


def determine_restart_phase(
    diff: DiffResult, content_changed: bool = False
) -> str:
    """Determine which pipeline phase to restart from based on diff scope.

    - If features were added, modified, or removed: "feature_analysis"
    - If no feature ID changes but content differs (UI-only): "architecture"
    - If nothing changed: "feature_analysis" (safe default)
    """
    if diff.added_features or diff.modified_features or diff.removed_features:
        return "feature_analysis"

    if content_changed:
        return "architecture"

    return "feature_analysis"


async def run_spec_diff(
    claude: "ClaudeRunner",
    old_structured_input: str,
    new_structured_input: str,
    working_dir: Path,
) -> str:
    """Compare old and new structured input to produce a change summary.

    Uses the ``spec_diff`` agent to identify what changed between two
    versions of a Structured Input document.

    Returns:
        A textual summary of spec changes.
    """
    from agentic_dev.orchestrator.agent_bridge import AgentRunConfig  # noqa: WPS433
    from agentic_dev.prompts.renderer import PromptRenderer  # noqa: WPS433

    config = AgentRunConfig(
        name="spec_diff",
        model="opus",
        permission_mode="bypassPermissions",
        allowed_tools=[],
        use_bare_mode=True,
        mcp_config=None,
        system_prompt=None,
    )

    renderer = PromptRenderer()
    prompt = renderer.render(
        "spec_diff.md.j2",
        {
            "old_structured_input": old_structured_input,
            "new_structured_input": new_structured_input,
            "constraints": [
                "Identify all added, removed, and modified features",
                "Identify changes to preferences, tech stack, and project type",
                "Identify changes to non-functional requirements",
                "Do not describe unchanged elements",
                "Be specific about what changed — include old and new values where applicable",
            ],
        },
    )

    result = await claude.run(
        agent=config,
        prompt=prompt,
        working_dir=working_dir,
    )
    return result.text
