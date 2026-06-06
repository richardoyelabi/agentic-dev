"""Aggregate per-track UAT reports into a single multi-track verdict.

The aggregator concatenates each track's UAT report under a track-named header,
then derives a top-level ``Overall Result`` line: ``PASS`` iff every per-track
report's overall result is also ``PASS``.
"""

from __future__ import annotations

import re


_OVERALL_RESULT_PATTERN = re.compile(
    r"^##\s*Overall Result:\s*(PASS|FAIL)\b",
    re.IGNORECASE | re.MULTILINE,
)


def _extract_verdict(report: str) -> str:
    """Return ``"PASS"`` or ``"FAIL"`` for a single per-track report."""
    match = _OVERALL_RESULT_PATTERN.search(report)
    if match is None:
        return "FAIL"
    return match.group(1).upper()


def aggregate_uat_reports(per_track: dict[str, str], label: str = "Track") -> str:
    """Combine N sub-reports into one report with a derived overall verdict.

    Returns markdown with a top-level ``## Overall Result:`` line followed by
    each sub-report under a ``# <label>: <name>`` header. ``label`` is ``Track``
    for the multi-track roll-up and ``Feature`` for the per-feature roll-up
    within a single track.
    """
    if not per_track:
        return "## Overall Result: FAIL\n\nNo UAT-capable tracks ran.\n"

    verdicts = {name: _extract_verdict(text) for name, text in per_track.items()}
    overall = "PASS" if all(v == "PASS" for v in verdicts.values()) else "FAIL"

    parts = [f"## Overall Result: {overall}", ""]
    parts.append(f"## Per-{label} Verdicts")
    for name in sorted(verdicts):
        parts.append(f"- **{name}**: {verdicts[name]}")
    parts.append("")
    for name in sorted(per_track):
        parts.append(f"# {label}: {name}")
        parts.append("")
        parts.append(per_track[name].strip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"
