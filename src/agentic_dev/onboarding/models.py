"""Data models for the onboarding module."""

from pydantic import BaseModel


class AnnotatedSource(BaseModel):
    """A source (codebase path or Figma URL) with an optional human annotation.

    The annotation helps agents understand what each source represents,
    e.g. "Frontend React app" or "Admin dashboard designs".
    """

    value: str
    annotation: str = ""

    @classmethod
    def parse(cls, raw: str) -> "AnnotatedSource":
        """Parse a raw CLI string into an AnnotatedSource.

        Supports the format ``value::annotation`` where the annotation is
        optional. Splits on the first ``::`` only, so URLs or annotations
        containing ``::`` are handled correctly.
        """
        if "::" in raw:
            value, annotation = raw.split("::", 1)
            return cls(value=value.strip(), annotation=annotation.strip())
        return cls(value=raw.strip(), annotation="")
