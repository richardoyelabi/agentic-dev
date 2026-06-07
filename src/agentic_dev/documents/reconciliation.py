"""Cross-document reconciliation of the feature/spec/sprint ID graph.

Independent LLM agents emit cross-referencing documents (features request,
track specs, sprint plan, API contract) that the pipeline later filters against
each other by ID. When the references don't line up — a feature scheduled in no
sprint, a sprint referencing an undefined feature, or a spec that names a
feature only in prose so :func:`scope_spec_to_features` silently drops it —
content is lost without warning. This module surfaces those mismatches as
:class:`Finding` objects so the design checkpoint (and the logs) can show them
before the expensive build runs.

Pure logic — no I/O. Mirrors :mod:`agentic_dev.documents.scoping`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from agentic_dev.documents.scoping import (
    extract_sprint_feature_ids,
    split_feature_sections,
)

ERROR = "ERROR"
WARN = "WARN"

# A bare feature token anywhere in prose: ``F004``. Used to tell "mentioned but
# not bracketed" (non-canonical) from "absent entirely" (coverage gap).
_BARE_FEATURE_RE = re.compile(r"\bF\d+\b")


class SprintLike(Protocol):
    """Structural type for the sprint fields reconciliation reads.

    ``SprintState`` satisfies this; declaring a Protocol keeps this module free
    of a dependency on :mod:`agentic_dev.state`.
    """

    sprint_number: int
    name: str
    scope_text: str
    tracks_in_scope: list[str]


@dataclass(frozen=True)
class Finding:
    """A single reconciliation result."""

    severity: str
    code: str
    message: str
    ids: tuple[str, ...] = ()


def has_errors(findings: Sequence[Finding]) -> bool:
    """True when any finding is ERROR-severity (should block the build)."""
    return any(f.severity == ERROR for f in findings)


def format_findings(findings: Sequence[Finding]) -> str:
    """Render findings as a markdown report for the design checkpoint."""
    if not findings:
        return (
            "# Reconciliation Report\n\n"
            "No cross-document inconsistencies found.\n"
        )
    lines = ["# Reconciliation Report", ""]
    for finding in findings:
        lines.append(
            f"- **{finding.severity}** ({finding.code}): {finding.message}"
        )
    return "\n".join(lines) + "\n"


def reconcile(
    features_text: str,
    sprints: Sequence[SprintLike],
    specs_by_track: dict[str, str],
    api_contract: str = "",
) -> list[Finding]:
    """Check that features, sprints, and specs cross-reference consistently.

    Returns findings ERROR-first, then in document/track order. See the module
    docstring for the failure modes this guards against.
    """
    findings: list[Finding] = []

    defined = {fid for fid, _ in split_feature_sections(features_text)}

    track_features: dict[str, set[str]] = {}
    all_sprint_refs: set[str] = set()
    for sprint in sprints:
        fids = extract_sprint_feature_ids(sprint.scope_text)
        all_sprint_refs |= fids
        for track in sprint.tracks_in_scope:
            track_features.setdefault(track, set()).update(fids)

    # 1. Orphan features: defined but scheduled in no sprint -> never built.
    for fid in sorted(defined - all_sprint_refs):
        findings.append(Finding(
            ERROR,
            "orphan_feature",
            f"Feature [{fid}] is defined in the features doc but scheduled in "
            f"no sprint — it will never be built.",
            (fid,),
        ))

    # 2. Dangling references: referenced anywhere but never defined.
    spec_refs: set[str] = set()
    for spec in specs_by_track.values():
        spec_refs |= extract_sprint_feature_ids(spec)
    spec_refs |= extract_sprint_feature_ids(api_contract)
    for fid in sorted((all_sprint_refs | spec_refs) - defined):
        findings.append(Finding(
            ERROR,
            "dangling_ref",
            f"Feature [{fid}] is referenced but has no '## Feature: [{fid}]' "
            f"section in the features doc — likely a typo or stale reference.",
            (fid,),
        ))

    # 3 & 4. Per-track spec coverage for each in-scope, defined feature.
    for track in sorted(track_features):
        spec = specs_by_track.get(track)
        if not spec or not spec.strip():
            continue
        bracketed = extract_sprint_feature_ids(spec)
        bare = set(_BARE_FEATURE_RE.findall(spec))
        prose_only = bare - bracketed
        for fid in sorted(track_features[track] & defined):
            if fid not in bare:
                findings.append(Finding(
                    WARN,
                    "spec_coverage_gap",
                    f"Feature [{fid}] is in scope for track '{track}' but is "
                    f"absent from its spec — the developer/UAT agent will have "
                    f"no spec for it.",
                    (fid,),
                ))
            elif fid in prose_only:
                findings.append(Finding(
                    WARN,
                    "noncanonical_ref",
                    f"Feature [{fid}] is referenced in track '{track}' spec "
                    f"only in prose (e.g. '({fid})'), never bracketed '[{fid}]', "
                    f"so spec-scoping and UAT selection silently miss it.",
                    (fid,),
                ))

    return findings
