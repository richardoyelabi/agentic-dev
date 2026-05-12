"""Wire schemas for LLM-as-parser extraction.

These models describe the JSON shape the LLM parser is asked to return when
extracting structured data from prose-rich agent outputs. They are
intentionally separate from the internal state models so the wire format and
the in-memory state can evolve independently. Each call site converts
``Parsed*`` -> internal model.
"""

from pydantic import BaseModel, Field


class ParsedSprintEntry(BaseModel):
    """One sprint as extracted by the LLM parser from a sprint plan document."""

    sprint_number: int
    name: str
    scope_text: str = ""
    needs_integration: bool = False
    integration_services: list[str] = Field(default_factory=list)
    tracks_in_scope: list[str] = Field(default_factory=list)


class ParsedSprintPlan(BaseModel):
    """The full set of sprints extracted from a sprint plan document."""

    sprints: list[ParsedSprintEntry] = Field(default_factory=list)
