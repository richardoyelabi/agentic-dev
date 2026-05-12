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


def aggregate_uat_reports(per_track: dict[str, str]) -> str:
    """Combine N per-track UAT reports into one multi-track report.

    Returns markdown with a top-level ``## Overall Result:`` line followed by
    each track's report under a ``# Track: <name>`` header.
    """
    if not per_track:
        return "## Overall Result: FAIL\n\nNo UAT-capable tracks ran.\n"

    verdicts = {name: _extract_verdict(text) for name, text in per_track.items()}
    overall = "PASS" if all(v == "PASS" for v in verdicts.values()) else "FAIL"

    parts = [f"## Overall Result: {overall}", ""]
    parts.append("## Per-Track Verdicts")
    for name in sorted(verdicts):
        parts.append(f"- **{name}**: {verdicts[name]}")
    parts.append("")
    for name in sorted(per_track):
        parts.append(f"# Track: {name}")
        parts.append("")
        parts.append(per_track[name].strip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"
