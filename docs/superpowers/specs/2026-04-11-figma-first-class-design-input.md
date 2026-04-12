# Figma as First-Class Design Input

**Date:** 2026-04-11
**Status:** Draft

## Context

Figma designs are currently treated as a secondary input in agentic-dev. During
onboarding (`new`, `adopt`), the Figma MCP agent extracts a text-based design
analysis that is concatenated into `user_input` and saved as `design_analyses`.
The architect reads this text to inform `frontend_spec`, and then the actual
Figma file is never referenced again.

This means:

1. **The frontend developer never sees the real design.** It codes from a text
   spec — a lossy translation of the visual source.
2. **The `update` command has no Figma support.** Design changes cannot trigger
   updates.
3. **The `sync` command stubs Figma** — it passes `figma_analysis: ""` and never
   reads the design analyses document.
4. **Design is appended to text input** in `new`, making it a second-class
   citizen rather than its own input channel.

Real teams work differently: designers provide Figma files, and developers
reference them directly while coding. This spec makes design a first-class input
on equal footing with text throughout the entire pipeline.

## Goals

- Frontend developer and QA agents reference actual Figma designs via MCP during
  sprinting
- Design has its own input channel, parallel to text, through the update pipeline
- `--from-figma` is available on the `update` command
- Consistent Figma handling across `new`, `adopt`, `update`, and `sync`

## Design

### 1. Persistent Figma URL Storage

**Problem:** Figma URLs are used during onboarding and discarded.

**Solution:** Write a `figma_sources` document to the doc store whenever Figma
URLs are provided. This document persists across pipeline phases and is readable
by the sprint runner.

Format:

```markdown
# Figma Sources

- URL: https://figma.com/file/ABC123
  Annotation: Main app UI

- URL: https://figma.com/file/DEF456
  Annotation: Admin dashboard
```

This format is agent-facing (passed as text to prompts). If future code needs to
parse URLs programmatically, a structured format should be considered.

**Files affected:**
- `src/agentic_dev/cli.py` — `new`, `adopt`, `update` commands write
  `figma_sources` doc when Figma URLs are provided

### 2. Frontend Developer Agent Gets Figma Access

**Problem:** The frontend developer agent has Figma MCP tools available
(inherited from user settings) but doesn't know to use them — no URLs, no
prompt instructions.

**Solution:**

**Sprint runner** (`src/agentic_dev/orchestrator/sprint_runner.py`):
- Before building `extra_context`, check if `figma_sources` doc exists
- If it does, check Figma MCP availability via `check_figma_mcp_available()`
- If MCP is available: add `figma_sources` content and set
  `figma_mcp_available: "true"` in `extra_context`
- If MCP is unavailable: log a warning event via `_event_log.warning()`, set
  `figma_mcp_available: "false"` in `extra_context`, still pass `figma_sources`
  so the template can fall back to text-based design analyses
- `figma_sources` is added to the shared `extra_context` dict — backend and
  integration templates simply will not reference the variable, so it is harmless

**Prompt template** (`src/agentic_dev/prompts/templates/frontend_developer.md.j2`):
- Add a conditional Figma section with MCP availability branching:

```jinja2
{% if figma_sources is defined and figma_sources %}
# Figma Design Reference

{% if figma_mcp_available == "true" %}
You have access to Figma MCP tools. Use them to reference the actual designs
while implementing.

**Figma is the visual source of truth.** Match the designs exactly for layout,
spacing, colors, typography, and component appearance. Use the Frontend Spec for
technical decisions (tech stack, state management, API integration).

{{ figma_sources }}

**Workflow:** Before implementing each component, use Figma MCP to inspect the
relevant design. After implementation, compare your output visually against the
design.
{% else %}
Figma MCP tools are not available. Use the design analyses in the Frontend Spec
as your visual reference. Match the described layout, colors, typography, and
spacing as closely as possible.

{{ figma_sources }}
{% endif %}
{% endif %}
```

**No changes to agent YAML definition** — MCP tools are inherited automatically
from user settings. The `allowed_tools` list only controls built-in tools.

### 3. Frontend QA Agent Gets Figma Access

**Problem:** Frontend QA only reviews code quality and spec compliance, not
visual fidelity.

**Solution:**

**Sprint runner**: `figma_sources` and `figma_mcp_available` are already in
`extra_context` (from Section 2). The `run_qa_cycle` function makes
`extra_context` available to both action and QA agents, so no additional sprint
runner changes are needed.

**Prompt template** (`src/agentic_dev/prompts/templates/frontend_qa.md.j2`):
- Add a conditional Figma section:

```jinja2
{% if figma_sources is defined and figma_sources %}
# Figma Design Reference

{% if figma_mcp_available == "true" %}
You have access to Figma MCP tools. Use them to verify visual fidelity.
{% else %}
Figma MCP tools are not available. Use the design analyses in the Frontend Spec
as your visual reference for evaluating design fidelity.
{% endif %}

{{ figma_sources }}
{% endif %}
```

- Add evaluation criterion #7 (conditional on Figma availability):

```
{% if figma_sources is defined and figma_sources %}
7. **Does it match the design?** — Components match the Figma design for layout,
   spacing, colors, typography, and visual hierarchy.
   {% if figma_mcp_available == "true" %}Use Figma MCP tools to compare.
   {% else %}Compare against the design analyses text.{% endif %}
{% endif %}
```

**No changes to QA agent YAML definition** — MCP access is inherited.

### 4. Parallel Design Input Channel for Updates

**Problem:** The `update` command has no Figma support. If we were to add it
naively, design would be concatenated into the text change input — still
second-class.

**Solution:** Design gets its own parallel input channel through the update
pipeline, on equal footing with text.

**Two first-class input channels:**

| | Text channel | Design channel |
|---|---|---|
| **CLI source** | `--from-file`, `--full-spec`, interactive | `--from-figma` |
| **Raw input doc** | `user_input` | `design_input` |
| **Change detection** | `change_request` (text of changes) | `design_changes` (diff summary produced by `design_diff` agent) |
| **Processed reference doc** | `structured_input` | `design_analyses` |

**Key difference between channels:** Text changes can be expressed as
incremental change requests. Design changes cannot — Figma MCP always returns
the full current state of the design. So the design channel always replaces
`design_analyses` wholesale, and a `design_diff` agent compares old vs new to
produce a `design_changes` summary that tells downstream agents what changed.

**CLI changes** (`src/agentic_dev/cli.py` — `update` command):
- Add `--from-figma` option (repeatable `list[str]`, same as `new`)
- Compatible with all other input modes: `--from-file`, `--full-spec`,
  interactive, or standalone
- When provided:
  1. Check Figma MCP prerequisites
  2. Read old `design_analyses` (before archiving) for diff comparison
  3. Run `analyze_figma_designs()` to extract new design analysis
  4. Write `design_input` doc (audit trail)
  5. Overwrite `design_analyses` with new analysis
  6. Run `design_diff` agent (old vs new) to produce `design_changes` summary
  7. Update `figma_sources` doc with new URLs

**Compatibility matrix:**

| Combination | Valid |
|---|---|
| `--from-figma` alone | Yes |
| `--from-figma` + `--from-file` | Yes |
| `--from-figma` + `--full-spec` | Yes |
| `--from-figma` + interactive | Yes |

**When `--from-figma` is used alone (no text input):**
- The text channel is skipped entirely — no interactive prompt fires
- `_start_update_cycle` accepts `change_input` as `str | None` (make the
  parameter optional with default `None`)
- When `change_input` is `None`, the `user_input` and `change_request` docs are
  not written
- The design channel drives the update: `design_input` and
  `design_change_request` are written
- Restart phase defaults to `ARCHITECTURE` (design-only changes primarily affect
  specs)

**When both text and Figma are provided:**
- Both channels write their respective docs
- Restart phase is determined by the text input logic (existing behavior)

**`_start_update_cycle` changes** (`src/agentic_dev/cli.py`):
- Accept optional `design_input: str | None = None` and
  `design_changes: str | None = None` parameters
- Write `user_input` doc only when `change_input` is provided
- Write `change_request` doc only when `change_input` is provided and
  `is_targeted` is True
- Write `design_input` doc when provided (audit trail)
- Always overwrite `design_analyses` with new design analysis
- Write `design_changes` doc when provided (diff summary)

**Engine changes** (`src/agentic_dev/orchestrator/engine.py`):
- `_update_extra_context()` includes `design_changes` from doc store when it
  exists, feeding the diff summary to all downstream phases (feature analysis,
  architecture, sprint planning) and sprint agents
- `_run_single_agent()` accepts optional `extra_context` parameter
- `_run_uat()` passes `extra_context` from `_update_extra_context()` so UAT
  receives both `change_request` and `design_changes`

**New agent: `design_diff`**

Agent definition (`src/agentic_dev/agents/definitions/design_diff.yml`):

```yaml
name: design_diff
description: "Compares old and new design analyses to produce a summary of what changed"
team: design_architecture
claude:
  model: opus
  permission_mode: bypassPermissions
  allowed_tools: []
  max_turns: 5
  max_budget_usd: 1.00
prompt_template: design_diff.md.j2
input_documents: [old_design_analyses, new_design_analyses]
output_documents: [design_changes]
qa_agent: null
constraints:
  - "Identify all added, removed, and modified pages"
  - "Identify all added, removed, and modified components with their visual changes"
  - "Identify all changed design tokens (colors, typography, spacing)"
  - "Identify navigation and user flow changes"
  - "Do not describe unchanged elements"
  - "Be specific about what changed — include old and new values where applicable"
```

Prompt template (`src/agentic_dev/prompts/templates/design_diff.md.j2`):
takes old and new design analyses, compares them, and outputs a structured
change summary organized by pages, components, tokens, and navigation.

### 5. Consistent Figma Handling Across `new` and `adopt`

**Problem:** In `new`, Figma analysis is concatenated into `user_input` (inside
the `if figma_sources:` block in the `new` command), treating design as part of
the text channel.

**Fix `new` command** (`src/agentic_dev/cli.py`):
- Stop appending Figma analysis to `user_input` (remove the `user_input =
  (user_input or "") + header + result.text` concatenation inside the Figma
  sources loop)
- Continue writing `design_analyses` doc (already done)
- Add: write `figma_sources` doc with URLs (alongside `design_analyses`)
- Fix the `if not user_input:` abort check: when `--from-figma` is the sole
  input (no text, no `--from-file`), `user_input` will be empty, but the
  command should NOT abort. Update the check to also consider whether
  `figma_sources` were provided: `if not user_input and not figma_sources:`
- `--from-figma` alone is valid for `new` — the design analysis feeds into
  `design_analyses` and the pipeline starts from `INPUT_PROCESSING` as normal.
  The input processor receives `user_input` (which may be empty or minimal) and
  `design_analyses` is available to the architect downstream.

**Fix `adopt` command** (`src/agentic_dev/cli.py`):
- Already passes `design_analyses` separately to `run_adoption()` — no change
  needed there. Note: the adopt command currently does NOT write a
  `figma_sources` document, so the URLs are discarded after analysis.
- Add: write `figma_sources` doc with URLs

### 6. Fix `sync` Command's Figma Integration

**Problem:** `sync` has Figma scaffolding but it's non-functional:
- `_detect_drift` passes `figma_analysis: ""` (hardcoded empty string)
- Drift report parser already handles `"figma"` scope and `"design_drift"`
  category
- `--from figma` resolution exists but only resolves figma-scoped items to
  `"to_spec"`

**Fixes** (`src/agentic_dev/orchestrator/sync.py`):
1. `_detect_drift` reads `design_analyses` from the doc store and passes it as
   `figma_analysis` instead of `""`
2. When `figma_sources` doc exists, pass the Figma URLs to the drift detector
   via the prompt context so it can use Figma MCP to compare current designs
   against specs/code. If Figma MCP is unavailable, the drift detector still
   receives `design_analyses` text for text-based comparison — same fallback
   pattern as the sprint runner (Section 2)

**Fix `--from figma` resolution** (`src/agentic_dev/cli.py` — `sync` command):

The resolution depends on the drift category:

| Drift category | `--from figma` resolution | Meaning |
|---|---|---|
| `design_drift` | `to_spec` | Specs should update to match current Figma designs |
| `in_code_not_spec` (figma scope) | `to_spec` | Code has something Figma doesn't show — update spec to note this |
| `in_spec_not_code` (figma scope) | `to_code` | Figma shows something code doesn't have — code should match |
| `difference` (figma scope) | `to_code` | Code diverges from Figma — code should match |

Non-figma-scoped items remain unresolved and fall through to interactive
resolution (existing behavior).

## Files Modified

| File | Change |
|---|---|
| `src/agentic_dev/cli.py` | Add `--from-figma` to `update`; fix `new` to stop concatenating Figma into `user_input`; write `figma_sources` in `new`/`adopt`/`update`; update `_start_update_cycle` for design channel with diff; fix `sync` `--from figma` resolution |
| `src/agentic_dev/orchestrator/engine.py` | Update `_update_extra_context` for `design_changes`; add `extra_context` to `_run_single_agent` and `_run_uat` |
| `src/agentic_dev/orchestrator/sprint_runner.py` | Read `figma_sources`, `design_changes`, check Figma MCP availability, pass to `extra_context` for all sprint cycles |
| `src/agentic_dev/orchestrator/sync.py` | Read `design_analyses` and `figma_sources` for drift detection instead of hardcoded empty string |
| `src/agentic_dev/onboarding/figma.py` | Add `write_figma_sources()` and `run_design_diff()` |
| `src/agentic_dev/prompts/templates/frontend_developer.md.j2` | Add conditional Figma reference and `design_changes` sections |
| `src/agentic_dev/prompts/templates/frontend_qa.md.j2` | Add conditional Figma, `design_changes` sections and visual fidelity criterion |
| `src/agentic_dev/prompts/templates/uat.md.j2` | Add conditional `change_request` and `design_changes` sections |
| `src/agentic_dev/agents/definitions/design_diff.yml` | **New** — agent definition for design change detection |
| `src/agentic_dev/prompts/templates/design_diff.md.j2` | **New** — prompt template for design_diff |

## Existing Code to Reuse

- `analyze_figma_designs()` / `analyze_figma_design()` — `src/agentic_dev/onboarding/figma.py`
- `check_figma_mcp_available()` — `src/agentic_dev/onboarding/figma.py`
- `check_mcp_prerequisites()` — `src/agentic_dev/mcp/setup.py`
- `AnnotatedSource.parse()` — `src/agentic_dev/onboarding/models.py`
- `_update_extra_context()` pattern — `src/agentic_dev/orchestrator/engine.py` (in `PipelineEngine`)
- `DocumentStore.write/read/exists/delete` — `src/agentic_dev/documents/store.py`

## Verification

1. **Unit tests:**
   - `figma_sources` doc is written in `new`, `adopt`, `update` when Figma URLs
     provided
   - `_start_update_cycle` accepts `change_input=None` when only design input
     is provided (Figma-only update)
   - `_start_update_cycle` writes `design_input`, `design_analyses`, and
     `design_changes` (diff summary) when design input provided
   - `_update_extra_context` includes both `change_request` and
     `design_changes` when the respective docs exist
   - Sprint runner passes `figma_sources`, `figma_mcp_available`, and
     `design_changes` in `extra_context`
   - UAT receives `extra_context` with `change_request` and `design_changes`
   - `--from-figma` is compatible with all other update input modes
   - `new` command no longer concatenates Figma analysis into `user_input`
   - `new` command with `--from-figma` alone does not abort (abort check
     accounts for Figma input)
   - `design_diff` agent is registered and loadable from `AgentRegistry`

2. **E2E tests:**
   - `update` with `--from-figma` alone triggers pipeline from `ARCHITECTURE`
   - `update` with `--from-figma` + `--from-file` processes both channels
   - `update` with `--from-figma` + `--full-spec` writes design docs
   - `new` with `--from-figma` writes `figma_sources` and `design_analyses` as
     separate docs, `user_input` does not contain Figma analysis
   - `sync` reads `design_analyses` for drift detection (non-empty
     `figma_analysis`)

3. **Edge case tests:**
   - `--from-figma` with invalid/unreachable Figma URL produces a clear error
   - Existing projects without `figma_sources` doc work without regression
   - `design_analyses` doc exists but `figma_sources` does not (legacy project)
     — sprint runner does not attempt Figma MCP access
   - Figma MCP unavailable during sprinting — warning logged, prompt falls back
     to text-based design reference

4. **Manual verification:**
   - Frontend developer agent prompt includes Figma and `design_changes`
     sections when respective docs exist
   - Frontend QA agent prompt includes visual fidelity criterion and
     `design_changes` scoping when respective docs exist
   - UAT prompt includes `change_request` and `design_changes` when in update
     mode
   - Drift detector receives non-empty `figma_analysis` when `design_analyses`
     exists
