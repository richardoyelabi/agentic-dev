"""LLM-as-parser helper.

Replaces brittle regex parsing of prose-rich agent output with a downstream
Claude call that returns JSON conforming to a Pydantic schema. Use for inputs
where the structural metadata is small but the document body contains prose
that may collide with regex markers (the original sprint-plan / drift-report
parsers were both vulnerable to this).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from agentic_dev.claude.runner import ClaudeRunner
from agentic_dev.exceptions import OutputParseError

T = TypeVar("T", bound=BaseModel)

_FENCED_JSON_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
_FENCED_ANY_RE = re.compile(r"```[a-zA-Z]*\s*\n(.*?)\n```", re.DOTALL)


@dataclass
class _ParserAgentConfig:
    """Minimal AgentConfig for one-shot JSON-extraction calls."""

    name: str = "llm_parser"
    model: str = "haiku"
    permission_mode: str = "default"
    allowed_tools: list[str] = field(default_factory=list)
    use_bare_mode: bool = True
    mcp_config: Path | None = None
    system_prompt: str | None = None


def _extract_json_block(text: str) -> str:
    """Pull the first JSON payload out of an LLM response.

    Prefers a ```json fenced block, then any fenced block, then a raw
    object/array span as a last resort. Returns the inner JSON string.
    """
    fenced = _FENCED_JSON_RE.search(text)
    if fenced:
        return fenced.group(1).strip()
    any_fenced = _FENCED_ANY_RE.search(text)
    if any_fenced:
        return any_fenced.group(1).strip()

    stripped = text.strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = stripped.find(opener)
        end = stripped.rfind(closer)
        if start != -1 and end != -1 and end > start:
            return stripped[start:end + 1]
    return stripped


def _build_prompt(
    *,
    extraction_prompt: str,
    schema_model: type[BaseModel],
    text: str,
    prior_error: str | None,
) -> str:
    schema_json = json.dumps(schema_model.model_json_schema(), indent=2)
    error_block = (
        f"Your previous response failed validation:\n{prior_error}\n"
        "Re-emit the JSON, fixing the issue.\n\n"
        if prior_error
        else ""
    )
    return (
        f"{error_block}{extraction_prompt}\n\n"
        "Return ONLY a single fenced ```json code block matching this JSON schema. "
        "Do not include any prose before or after the block.\n\n"
        "Schema:\n"
        f"```json\n{schema_json}\n```\n\n"
        "Document to parse:\n"
        "<<<DOCUMENT\n"
        f"{text}\n"
        "DOCUMENT>>>\n"
    )


async def parse_with_llm(
    *,
    claude: ClaudeRunner,
    text: str,
    schema_model: type[T],
    extraction_prompt: str,
    working_dir: Path,
    sanity_check: Callable[[T], None] | None = None,
    max_attempts: int = 2,
    agent_name: str = "llm_parser",
    model: str = "haiku",
) -> T:
    """Ask Claude to extract a structured object from a prose document.

    Args:
        claude: Live ClaudeRunner used to invoke the model.
        text: The source document the LLM should read.
        schema_model: Pydantic class describing the expected output shape.
        extraction_prompt: Site-specific instructions (e.g. "Extract every
            sprint defined in the plan below").
        working_dir: Directory to run Claude in (any path the runner accepts).
        sanity_check: Optional callable that raises if the parsed result fails
            a domain-specific invariant (e.g. count mismatch, duplicate IDs).
            Treated as a validation failure for retry purposes.
        max_attempts: Total attempts including the first.
        agent_name: Used in error messages and runner telemetry.
        model: Model alias passed to the runner (cheap models are fine).

    Returns:
        A validated instance of ``schema_model``.

    Raises:
        OutputParseError: When the LLM cannot produce schema-valid output
        after ``max_attempts``, or when the sanity check rejects the result
        on the final attempt.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    config = _ParserAgentConfig(name=agent_name, model=model)
    last_error: str = ""

    for attempt in range(max_attempts):
        prompt = _build_prompt(
            extraction_prompt=extraction_prompt,
            schema_model=schema_model,
            text=text,
            prior_error=last_error if attempt > 0 else None,
        )

        result = await claude.run(
            agent=config,
            prompt=prompt,
            working_dir=working_dir,
        )

        raw = result.text or ""
        if not raw.strip():
            last_error = "model returned empty text"
            continue

        json_blob = _extract_json_block(raw)
        try:
            payload = json.loads(json_blob)
        except json.JSONDecodeError as exc:
            last_error = f"JSON decode error: {exc}"
            continue

        try:
            parsed = schema_model.model_validate(payload)
        except ValidationError as exc:
            last_error = f"schema validation error: {exc}"
            continue

        if sanity_check is not None:
            try:
                sanity_check(parsed)
            except Exception as exc:  # noqa: BLE001 — propagate as last_error
                last_error = f"sanity check failed: {exc}"
                continue

        return parsed

    raise OutputParseError(
        agent_name=agent_name,
        message=f"LLM parser failed after {max_attempts} attempt(s): {last_error}",
    )
