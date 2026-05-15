# Architecture

## Overview

`agentic-dev` is a Python CLI that orchestrates Claude Code CLI sessions
as an autonomous software development agency. It is a **process enforcer**
that runs against the directory you invoke it from: there is no global
project registry, no app-name argument, and no scaffolder for new
projects. On first invocation in a directory, it scaffolds a
`.agentic-dev/` metadata folder in place and drives a deterministic
pipeline; on subsequent invocations it dispatches on persisted state.

```
┌─────────────────────────────────────────────────────────┐
│                     CLI (Typer)                          │
├─────────────────────────────────────────────────────────┤
│                  Pipeline Engine                         │
│  ┌──────────┐  ┌──────────┐  ┌────────────┐            │
│  │ QA Cycle │  │  Sprint  │  │ Checkpoint │            │
│  │          │  │  Runner  │  │   System   │            │
│  └──────────┘  └──────────┘  └────────────┘            │
├─────────────────────────────────────────────────────────┤
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐ │
│  │  Claude  │  │  Agent   │  │  Prompt   │  │  Doc   │ │
│  │  Runner  │  │ Registry │  │ Renderer  │  │ Store  │ │
│  └──────────┘  └──────────┘  └──────────┘  └────────┘ │
├─────────────────────────────────────────────────────────┤
│  ┌──────────┐  ┌───────────┐  ┌──────────┐  ┌────────┐│
│  │  State   │  │ Discovery │  │Workspace │  │  UAT   ││
│  │ Manager  │  │ + Tracks  │  │ Manager  │  │Subsys. ││
│  └──────────┘  └───────────┘  └──────────┘  └────────┘│
└─────────────────────────────────────────────────────────┘
         │                │
         ▼                ▼
  .agentic-dev/     Claude Code CLI
  (state.json)      (subprocess)
```

## Module Responsibilities

### `cli.py`
User-facing Typer commands (`work`, `resume`, `remediate`, `status`,
`config`, `logs`, `cost`, `tracks`). Every command resolves the project
directory by walking up from `cwd` looking for `.agentic-dev/`; none take
an app-name argument.

### `orchestrator/engine.py`
Finite-state-machine driver. Advances through phases, dispatches to the QA
cycle and sprint runner, evaluates checkpoints, and handles pipeline-level
rate-limit pauses. Splits the architect's multi-document output into
per-track `<track>_spec.md` files (see `_run_architecture` around
[engine.py:345](../src/agentic_dev/orchestrator/engine.py#L345)).

### `orchestrator/qa_cycle.py`
Reusable action-agent → QA-agent → optional-correction loop. Every agent
that has a `qa_agent` field in its definition is driven through this.

### `orchestrator/sprint_runner.py`
Executes a single sprint by running one generic `developer` + `qa` pair
per in-scope track, then an `integration` + `integration_qa` pair when the
sprint plan calls for it. The track's `kind` is passed into the prompt so
the developer agent gets kind-specific guidance.

### `orchestrator/agent_bridge.py`
Bridges the YAML `AgentDefinition` to the runtime `RunnerConfig` consumed
by `claude/runner.py`. Carries `use_bare_mode` through unchanged.

### `orchestrator/checkpoint.py`
Configurable pause points. Default: pause after design (architecture +
sprint plan). See `CheckpointConfig` (`after_design`, `after_each_sprint`,
`before_uat`).

### `orchestrator/uat_composer.py`
Composes the change-request input used when the user runs
`agentic-dev remediate` — wraps the last UAT report as a remediation
prompt so the next pipeline cycle treats it as the change input.

### `orchestrator/shutdown.py`
Cooperative shutdown handling for graceful cancellation of in-flight
agents.

### `discovery/agent.py`
Runs a Claude agent that walks the project on first invocation and emits
a list of `Track(name, path, kind, uat_kind)` values. Output is strict
JSON (`{"tracks": [...], "reasoning": "..."}`).

### `discovery/override.py`
Loads an `agentic-dev.yaml` from the project root if present, returning
its `tracks:` list. When the override exists, the Claude discovery agent
is skipped entirely.

### `onboarding/analyzer.py`
Runs per-track Claude agents in parallel to summarise existing code in
each track. Each track gets a `track_<name>_analysis.md` artifact; the
combined `existing_code_analyses.md` is fed to the architect so it
reverse-engineers specs that reflect what's there.

### `onboarding/figma.py`
Figma helpers. `analyze_figma_designs()` runs Claude with the Figma MCP to
extract design analyses. `write_figma_sources()` persists Figma URLs as
the `figma_sources` doc. `run_design_diff()` invokes the `design_diff`
agent to compare old vs new design analyses. `check_figma_mcp_available()`
checks for the Figma MCP server in Claude Code settings.

### `claude/runner.py`
Async subprocess wrapper around the `claude` CLI. Builds the command from
an agent's `RunnerConfig`. In print mode, the rendered prompt is passed
immediately after `-p` (before flags like `--allowedTools`) so the CLI
does not parse the prompt as an extra tool name.

### `agents/registry.py`
Loads agent definitions from YAML files in
`src/agentic_dev/agents/definitions/`. Provides lookup by name and team.

### `agents/base.py`
Pydantic models for the agent YAML schema: `ClaudeConfig`,
`AgentDefinition`, `RunnerConfig`. `use_bare_mode` defaults to `True`.

### `prompts/renderer.py`
Jinja2 template engine. Renders agent prompts with `input_documents`,
`constraints`, and (in correction mode) `previous_output` + `qa_feedback`.
Partials in `_partials/` provide reusable blocks for API contract context,
sprint scope, and correction instructions.

### `documents/store.py`
Reads and writes agent artifacts under `.agentic-dev/artifacts/`. Holds
the document-name → filename mapping.

### `documents/diff.py`
`run_spec_diff()` invokes the `spec_diff` agent to compare old vs new
structured input and produce a `spec_changes` summary, consumed by
downstream agents during update cycles.

### `documents/scoping.py`
Selects the subset of documents a sprint or track needs as input, keeping
prompts focused.

### `state/manager.py`
Persists `PipelineState` as JSON. Supports atomic writes and per-phase
history snapshots in `.agentic-dev/history/`.

### `state/models.py`
Pydantic models for the state machine: `PipelinePhase`, `SprintStatus`,
`SprintState`, `PipelineState`, `AgentRunRecord`.

### `state/transitions.py`
Pure-logic helpers for transitioning between phases —
`resume_from_failure()` clears error/`failed_at_phase` before resume.

### `workspace/manager.py`
Scaffolder for the `.agentic-dev/` metadata directory in the project
root. Has **no** global registry and no app-name concept — `ensure_scaffold`
is the single entry point. `workspace/claude_md.py` writes per-track
`CLAUDE.md` files; `workspace/git.py` provides the helpers used to commit
agent artifacts to the project repo.

### `config.py`
Module-level constants (paths, model IDs, rate-limit settings) and the
`ProjectConfig` Pydantic model persisted to `.agentic-dev/config.json`.
`ProjectConfig` fields: `app_name`, `tracks`, `sources`, `checkpoint`,
`uat_mode`. `resolve_project_dir()` walks upward from `cwd` looking for
`.agentic-dev/` — there is no global registry. `load_project_config()`
migrates older config files: the keys `directory_map`, `frontend_kind`,
and `sync_ignores` are treated as legacy and dropped.

### `tracks.py`
`Track(name, path, kind, uat_kind)` and helpers: `default_tracks()`,
`expected_architecture_docs()` (which per-track spec names are expected
from the architect for a given track list), `TrackProgress`.

### `mcp/claude_settings.py`
Discovers MCP servers from Claude Code's native settings files
(`~/.claude/settings.json`, project `.claude/settings.json`,
`.claude/settings.local.json`). Provides fuzzy matching by service name.
Agents inherit configured MCP servers automatically — no `--mcp-config`
flag is needed.

### `mcp/catalog.py`
Text-based service detection via regex patterns. Scans sprint plan text
for references to known services (figma, github, stripe, supabase).

### `mcp/setup.py`
Rich-formatted prerequisite validation and guided setup helpers. Checks
Claude Code settings for configured MCP servers and points the user at
`claude mcp add` or the Claude Code OAuth UI.

### `uat/dispatcher.py`
Pure-logic dispatch from `track.uat_kind` to a concrete UAT agent name
(`uat_<uat_kind>`). The desktop case picks between `uat_desktop_electron`
and `uat_desktop_tauri` based on a `desktop_framework` header parsed from
the track spec by `_read_desktop_framework()`. Invalid combinations raise
`ValueError`.

### `uat/prereqs.py`
Runtime prereq probes for each per-kind UAT agent. Checks that driver
tools are not only on PATH but actually usable (e.g. `maestro --version`
plus `maestro doctor`; `flutter --version` plus a booted non-web device;
`tauri-driver --version`; Playwright MCP availability). Writes
`uat_prereqs_<track>.md` artifacts that the per-track UAT agent reads
before starting.

### `uat/validator.py`
Code-level enforcement of the false-PASS invariant. Parses the UAT report
after the action agent completes and rewrites `Overall: PASS` to `FAIL`
(prepending a `## Validator Override` section) when any of four structural
rules fail: no runtime AC, runtime PASS without artifacts, all-`none`
drivers with overall PASS in `uat_mode: full`, or any PASS AC lacking
concrete `Evidence:` bullets.

### `uat/aggregator.py`
Reads each `uat_report_<track>.md` and emits the combined `uat_report.md`
with a single `## Overall Result: PASS|FAIL` line. PASS iff every track
passed.

### `concurrency.py`
Helpers for parallel execution of independent per-track agent runs
(track analysis, per-track sprint development, per-track UAT).

### `logging/`
Structured event logging (JSONL run logs + per-agent dumps in
`.agentic-dev/logs/agent_dumps/`).

## Data Flow

Text and design are parallel input channels that merge into
`extra_context` flowing to all downstream agents.

1. User input (positional / `--from-file` / stdin) → Input Processor →
   Structured Input.
2. Figma URLs (`--from-figma`) → Figma Analyzer → Design Analyses +
   Figma Sources (stored independently of the text channel).
3. First-run only: each track is summarised by the onboarding Analyzer →
   `track_<name>_analysis.md` (per track) +
   `existing_code_analyses.md` (concatenated).
4. On update mode: old + new Structured Input → Spec Diff →
   `spec_changes` (consumed by all downstream agents).
5. On update mode with `--from-figma`: old + new Design Analyses →
   Design Diff → `design_changes`.
6. Structured Input → Feature Analyst (+QA) → Features Request.
7. Features Request + Structured Input + `existing_code_analyses` →
   Architect (+QA) → multi-document output, split by the engine into
   `<track>_spec.md` per track (plus `api_contract.md` when any track
   has `kind=api`).
8. All specs → Sprint Planner (+QA) → Sprint Plan with per-sprint
   `**Tracks in scope:**` lines.
9. Per sprint, per in-scope track: track spec + API contract (if relevant)
   + sprint scope + `track.kind` → `developer` (+ `qa`) → code in the
   track directory. Frontend tracks also receive Figma Sources and a
   `figma_mcp_available` flag.
10. Per-sprint integration step (when scoped): `integration` +
    `integration_qa` → `integration_guide.md` and integration code.
11. Pre-UAT: `uat/prereqs.py` writes `uat_prereqs_<track>.md` and creates
    the artifacts directory `.agentic-dev/uat/<run_id>/evidence/<track>/`.
12. Per track with `uat_kind`: dispatcher picks the UAT agent →
    runtime-drives the product → `uat_report_<track>.md` → false-PASS
    validator → `uat_qa`. UAT agents launch long-running drivers in the
    background and poll them via the `Monitor` tool.
13. `uat/aggregator.py` rolls every per-track report into a single
    `uat_report.md` with one `## Overall Result: PASS|FAIL` line.
14. On `FAIL`, the user runs `agentic-dev remediate`, which composes the
    UAT report as a change request and re-enters the pipeline from
    `INPUT_PROCESSING` with `mode=remediate` and an incremented
    `remediation_cycle` counter.

## State machine

```
IDLE
 └─► INPUT_PROCESSING ─► INPUT_PROCESSING_QA
      └─► FEATURE_ANALYSIS ─► FEATURE_ANALYSIS_QA
           └─► ARCHITECTURE ─► ARCHITECTURE_QA
                └─► SPRINT_PLANNING ─► SPRINT_PLANNING_QA
                     └─► DESIGN_CHECKPOINT
                          └─► SPRINTING
                               └─► UAT ─► UAT_QA
                                    └─► COMPLETE
```

Any phase can transition to `FAILED`. `COMPLETE` and `FAILED` are the
terminal states. `COMPLETE` can transition back to `FEATURE_ANALYSIS`
(when a new `work` prompt arrives as an update) or to `INPUT_PROCESSING`
(during `remediate`). The `state.mode` field (`new` / `update` /
`remediate`) tells the engine which prompt template variants to render.

The legacy `ADOPTING / ADOPTED / SYNCING` phases were removed alongside
the `new`, `adopt`, `update`, and `sync` commands; first-run adoption is
now handled by the discovery + analyzer pass inside `work`, and drift
is handled by editing specs and re-running the pipeline.

## UAT subsystem

The single `uat` agent of earlier versions was replaced by a family of
per-kind agents dispatched by `track.uat_kind`:

| `uat_kind` | Agent | Driver |
|---|---|---|
| `web` | `uat_web` | Playwright MCP |
| `api` | `uat_api` | `curl` / `httpx` via Bash |
| `cli` | `uat_cli` | subprocess via Bash |
| `mobile` | `uat_mobile` | Maestro (with fallback to the project's own integration tests) |
| `desktop` (electron) | `uat_desktop_electron` | Playwright attached via CDP |
| `desktop` (tauri) | `uat_desktop_tauri` | `tauri-driver` (WebDriver) |

`uat/dispatcher.py:pick_uat_agent()` is the single dispatch entry point.
The desktop case reads a `desktop_framework:` header out of the track
spec to pick between Electron and Tauri.

UAT agents run long-lived drivers (browsers, simulators, dev servers) in
the background and poll progress through the `Monitor` tool, so a single
UAT cycle can drive a full real-product run without blocking.
