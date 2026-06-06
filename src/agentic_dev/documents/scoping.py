"""Sprint-scoped spec filtering to reduce token usage in agent prompts.

Spec documents produced by the architect agent use structured IDs like
``### [M001] User`` with ``**Features:** [F001], [F002]`` lines that
cross-reference feature IDs from the sprint plan.  This module extracts
the relevant feature IDs from a sprint scope and filters spec sections
so that only sprint-relevant content is passed to developer agents.
"""

from __future__ import annotations

import re


_FEATURE_REF_RE = re.compile(r"\[(?:EXISTING-|DELETED-)?(F\d+)\]")

_L3_HEADER_RE = re.compile(r"^### ")
_L2_HEADER_RE = re.compile(r"^## ")
_L1_HEADER_RE = re.compile(r"^# ")
_FEATURES_LINE_RE = re.compile(
    r"^\s*-\s+\*\*Features?:\*\*\s*(.+)", re.IGNORECASE | re.MULTILINE
)

# A feature entry in the features-request doc: ``## Feature: [F001] Title``.
_FEATURE_HEADER_RE = re.compile(r"^##\s+Feature:\s*\[(F\d+)\]", re.MULTILINE)


def extract_sprint_feature_ids(sprint_scope: str) -> set[str]:
    """Extract bare feature IDs from a sprint scope document.

    Matches ``[F001]``, ``[EXISTING-F001]``, ``[DELETED-F001]`` patterns
    and returns the bare ID (e.g. ``{"F001", "F002"}``).
    """
    return set(_FEATURE_REF_RE.findall(sprint_scope))


def scope_spec_to_features(spec_text: str, feature_ids: set[str]) -> str:
    """Filter a spec document to sections relevant to *feature_ids*.

    **Preserves:**
    - All level-1 (``#``) and level-2 (``##``) headers and their
      non-subsection content (Tech Stack, Error Handling, etc.).
    - Level-3 (``###``) sections whose ``**Features:**`` /
      ``**Feature:**`` line references at least one ID in *feature_ids*.
    - Level-3 sections *without* a ``Features`` line (shared components,
      general infrastructure, etc.).

    **Omits:**
    - Level-3 sections whose ``Features`` line references *none* of the
      given IDs.

    Returns the original text unchanged when *feature_ids* is empty.
    """
    if not feature_ids or not spec_text.strip():
        return spec_text

    lines = spec_text.splitlines(keepends=True)
    result: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]

        if _L3_HEADER_RE.match(line):
            section_lines = [line]
            i += 1
            while i < len(lines):
                next_line = lines[i]
                if (
                    _L3_HEADER_RE.match(next_line)
                    or _L2_HEADER_RE.match(next_line)
                    or _L1_HEADER_RE.match(next_line)
                ):
                    break
                section_lines.append(next_line)
                i += 1

            section_text = "".join(section_lines)
            features_match = _FEATURES_LINE_RE.search(section_text)

            if features_match:
                referenced = set(_FEATURE_REF_RE.findall(features_match.group(1)))
                if referenced & feature_ids:
                    result.extend(section_lines)
            else:
                result.extend(section_lines)
        else:
            result.append(line)
            i += 1

    return "".join(result)


def split_feature_sections(features_text: str) -> list[tuple[str, str]]:
    """Split a features-request doc into one self-contained doc per feature.

    Splits on ``## Feature: [FNNN]`` level-2 headers. Each returned text is a
    valid single-feature features request: the preamble before the first
    feature (e.g. the ``# Features Request`` title) followed by exactly that one
    ``## Feature:`` section. Returns ``[]`` when the doc has no feature headers,
    so callers can fall back to running the whole doc in one session.
    """
    matches = list(_FEATURE_HEADER_RE.finditer(features_text))
    if not matches:
        return []
    preamble = features_text[: matches[0].start()].strip()
    sections: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = (
            matches[idx + 1].start()
            if idx + 1 < len(matches)
            else len(features_text)
        )
        feature_id = match.group(1)
        body = features_text[start:end].strip()
        unit = (f"{preamble}\n\n{body}" if preamble else body) + "\n"
        sections.append((feature_id, unit))
    return sections
