"""Wire schemas for LLM-as-parser extraction.

These models describe the JSON shape the LLM parser is asked to return when
extracting structured data from prose-rich agent outputs. They are
intentionally separate from the internal state models (``SprintState``,
``DriftItem``) so the wire format and the in-memory state can evolve
independently. Each call site converts ``Parsed*`` -> internal model.
"""

from typing import Literal

from pydantic import BaseModel, Field


class ParsedSprintEntry(BaseModel):
    """One sprint as extracted by the LLM parser from a sprint plan document."""

    sprint_number: int
    name: str
    scope_text: str = ""
    needs_integration: bool = False
    integration_services: list[str] = Field(default_factory=list)


class ParsedSprintPlan(BaseModel):
    """The full set of sprints extracted from a sprint plan document."""

    sprints: list[ParsedSprintEntry] = Field(default_factory=list)


class ParsedDriftItem(BaseModel):
    """One drift item as extracted by the LLM parser from a drift report."""

    id: str
    scope: Literal["api", "frontend", "backend", "figma"]
    category: Literal[
        "in_code_not_spec", "in_spec_not_code", "difference", "design_drift",
    ]
    description: str
    source_file: str | None = None
    spec_reference: str | None = None


class ParsedDriftReport(BaseModel):
    """The full set of drift items extracted from a drift report document."""

    items: list[ParsedDriftItem] = Field(default_factory=list)
    summary: str = ""
