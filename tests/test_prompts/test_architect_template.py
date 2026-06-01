"""Tests for the architect prompt template.

Locks in two behaviour changes that come with giving the architect
codebase-read tools:

- the prompt no longer forbids tool use outright (the YAML now grants
  ``Read``/``Glob``/``Grep``)
- the prompt guides the architect to drill into specific files when the
  pre-computed code analyses are too coarse
"""

from agentic_dev.prompts.renderer import PromptRenderer


def _render(extra: dict | None = None) -> str:
    renderer = PromptRenderer()
    ctx = {
        "tracks": [],
        "features": "",
        "structured_input": "",
        "constraints": [],
        "correction_mode": False,
        "figma_mcp_available": "false",
    }
    if extra:
        ctx.update(extra)
    return renderer.render("architect.md.j2", ctx)


def test_prompt_no_longer_forbids_all_tool_use():
    rendered = _render()
    assert "Do not use any tools" not in rendered


def test_prompt_still_requires_plaintext_response_channel():
    """We dropped 'no tools' but kept 'write response as plain text'."""
    rendered = _render()
    assert "plain text" in rendered.lower()


def test_drill_down_guidance_present_when_analyses_provided():
    rendered = _render({"existing_code_analyses": "track a: ..."})
    lowered = rendered.lower()
    assert "read" in lowered
    assert "do not invent" in lowered
