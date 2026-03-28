"""Parser for Claude CLI JSON output and multi-document splitting."""

import json
import re

from agentic_dev.claude.runner import ClaudeResult
from agentic_dev.config import DOCUMENT_SEPARATOR_PATTERN
from agentic_dev.exceptions import OutputParseError


class OutputParser:
    """Parses structured output from Claude CLI invocations."""

    @staticmethod
    def parse_json_output(raw: str, agent_name: str = "unknown") -> ClaudeResult:
        """Parse raw JSON string from Claude CLI --output-format json.

        Args:
            raw: The raw JSON string from stdout.
            agent_name: Agent name used in error messages.

        Returns:
            A ClaudeResult with fields extracted from the JSON.

        Raises:
            OutputParseError: If the JSON cannot be parsed.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OutputParseError(
                agent_name=agent_name,
                message=f"Invalid JSON: {exc}",
            ) from exc

        return ClaudeResult(
            text=data.get("result", ""),
            session_id=data.get("session_id"),
            cost_usd=float(data.get("cost_usd", 0.0)),
            exit_code=0,
            raw_json=data,
        )

    @staticmethod
    def split_documents(
        text: str, expected_documents: list[str], agent_name: str = "unknown"
    ) -> dict[str, str]:
        """Split multi-document output on ``<!-- DOCUMENT: name -->`` markers.

        If ``expected_documents`` contains exactly one name and no markers are
        present in the text, the entire text is returned as that document.

        Args:
            text: The full output text potentially containing document markers.
            expected_documents: Ordered list of document names to extract.
            agent_name: Agent name used in error messages.

        Returns:
            A dict mapping document name to its content (stripped).

        Raises:
            OutputParseError: If any expected marker is missing.
        """
        # Single-document shortcut: no markers needed
        if len(expected_documents) == 1:
            marker_match = re.search(DOCUMENT_SEPARATOR_PATTERN, text)
            if not marker_match:
                return {expected_documents[0]: text.strip()}

        # Find all markers and their positions
        markers = list(re.finditer(DOCUMENT_SEPARATOR_PATTERN, text))
        found_names = [m.group(1) for m in markers]

        # Verify all expected documents have markers
        for name in expected_documents:
            if name not in found_names:
                raise OutputParseError(
                    agent_name=agent_name,
                    message=f"Missing document marker for '{name}'. "
                    f"Found markers: {found_names}",
                )

        # Extract content between markers
        result: dict[str, str] = {}
        for i, marker in enumerate(markers):
            doc_name = marker.group(1)
            start = marker.end()
            end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
            result[doc_name] = text[start:end].strip()

        return result
