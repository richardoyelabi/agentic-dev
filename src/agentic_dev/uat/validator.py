"""Code-level validator for UAT reports — enforces the false-PASS invariant.

Runs after the UAT agent completes and before the uat_qa agent reviews. See
the design spec section 7 for the rejection rules:

- **Rule 1** (``uat_mode=full``): no AC has ``Verification mode: runtime``.
- **Rule 2**: an AC with ``Result: PASS`` + ``Verification mode: runtime`` has
  no ``Artifacts:`` entries.
- **Rule 3** (``uat_mode=full``): overall ``PASS`` but every AC has ``Driver: none``.
- **Rule 4**: any AC with ``Result: PASS`` has no ``Evidence:`` bullets.

When any rule fires, the overall verdict is rewritten to ``FAIL`` and a
``## Validator Override`` section is prepended listing the triggered rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


_AC_HEADER_RE = re.compile(r"^###\s+\[AC-\d+\]", re.MULTILINE)
_OVERALL_RESULT_RE = re.compile(
    r"^(##\s*Overall\s*Result:\s*)(PASS|FAIL)", re.IGNORECASE | re.MULTILINE
)

_RESULT_RE = re.compile(r"\*\*Result:\*\*\s*(PASS|FAIL)", re.IGNORECASE)
_MODE_RE = re.compile(r"\*\*Verification mode:\*\*\s*(runtime|spec_trace|skipped)", re.IGNORECASE)
_DRIVER_RE = re.compile(r"\*\*Driver:\*\*\s*([A-Za-z_]+)")
_EVIDENCE_HEADER_RE = re.compile(r"\*\*Evidence:\*\*")
_ARTIFACTS_HEADER_RE = re.compile(r"\*\*Artifacts:\*\*")


@dataclass
class _AC:
    result: str
    mode: str
    driver: str
    evidence_bullets: int
    artifacts_bullets: int


def validate_uat_report(
    report: str, uat_mode: Literal["spec_only", "full"]
) -> str:
    """Return the report unchanged, or rewritten with a Validator Override section.

    The validator is structural — it parses the report format and applies the
    false-PASS rules mechanically. See module docstring for rule semantics.
    """
    acs = _parse_acs(report)
    overall = _parse_overall(report)
    if overall != "PASS":
        return report

    triggered = _evaluate_rules(acs, overall, uat_mode)
    if not triggered:
        return report

    rewritten = _OVERALL_RESULT_RE.sub(
        lambda m: f"{m.group(1)}FAIL", report, count=1
    )
    override = _render_override(triggered)
    return override + rewritten


def _parse_acs(report: str) -> list[_AC]:
    """Extract each AC block's structured fields."""
    positions = [m.start() for m in _AC_HEADER_RE.finditer(report)]
    if not positions:
        return []
    positions.append(len(report))
    acs: list[_AC] = []
    for start, end in zip(positions, positions[1:]):
        block = report[start:end]
        result_match = _RESULT_RE.search(block)
        mode_match = _MODE_RE.search(block)
        driver_match = _DRIVER_RE.search(block)
        acs.append(_AC(
            result=(result_match.group(1).upper() if result_match else "FAIL"),
            mode=(mode_match.group(1).lower() if mode_match else "skipped"),
            driver=(driver_match.group(1).lower() if driver_match else "none"),
            evidence_bullets=_count_bullets_after(block, _EVIDENCE_HEADER_RE),
            artifacts_bullets=_count_bullets_after(block, _ARTIFACTS_HEADER_RE),
        ))
    return acs


def _count_bullets_after(block: str, header_re: re.Pattern[str]) -> int:
    """Count non-empty bullet lines after the given header, until the next field."""
    match = header_re.search(block)
    if not match:
        return 0
    tail = block[match.end():]
    count = 0
    for line in tail.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Next field header (e.g. "- **Artifacts:**") ends the list region.
        if stripped.startswith("- **"):
            break
        # Indented sub-bullet counts as a bullet entry.
        if line.startswith("  -") or line.startswith("    -"):
            count += 1
            continue
        if stripped.startswith("- "):
            count += 1
            continue
        # Anything else ends the list region.
        break
    return count


def _parse_overall(report: str) -> str:
    match = _OVERALL_RESULT_RE.search(report)
    if not match:
        return "FAIL"
    return match.group(2).upper()


def _evaluate_rules(
    acs: list[_AC], overall: str, uat_mode: Literal["spec_only", "full"]
) -> list[tuple[str, str]]:
    """Return a list of (rule_id, description) for every triggered rule."""
    triggered: list[tuple[str, str]] = []

    if uat_mode == "full" and overall == "PASS":
        if not any(ac.mode == "runtime" for ac in acs):
            triggered.append((
                "Rule 1",
                "uat_mode=full requires at least one acceptance criterion with "
                "Verification mode: runtime, but none was present.",
            ))

    for i, ac in enumerate(acs, start=1):
        if ac.result == "PASS" and ac.mode == "runtime" and ac.artifacts_bullets == 0:
            triggered.append((
                "Rule 2",
                f"AC-{i:03d} has Result=PASS with Verification mode=runtime "
                "but no Artifacts entries — runtime claims require concrete artifacts.",
            ))

    if uat_mode == "full" and overall == "PASS":
        if acs and all(ac.driver == "none" for ac in acs):
            triggered.append((
                "Rule 3",
                "uat_mode=full overall PASS requires at least one acceptance "
                "criterion whose Driver is not 'none', but every AC reported Driver=none.",
            ))

    for i, ac in enumerate(acs, start=1):
        if ac.result == "PASS" and ac.evidence_bullets == 0:
            triggered.append((
                "Rule 4",
                f"AC-{i:03d} has Result=PASS but the Evidence section is empty; "
                "PASS verdicts must be supported by concrete evidence.",
            ))

    return triggered


def _render_override(triggered: list[tuple[str, str]]) -> str:
    """Render the `## Validator Override` prefix section."""
    lines = [
        "## Validator Override",
        "",
        "The automated UAT validator rewrote the overall verdict from **PASS** "
        "to **FAIL** because the following structural checks failed:",
        "",
    ]
    for rule_id, description in triggered:
        lines.append(f"- **{rule_id}:** {description}")
    lines.extend([
        "",
        "See `docs/superpowers/specs/2026-04-17-multi-frontend-runtime-uat-design.md` "
        "section 7 for the invariant.",
        "",
        "---",
        "",
        "",
    ])
    return "\n".join(lines)
