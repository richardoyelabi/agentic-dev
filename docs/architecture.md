# Architecture

## Overview

Agentic-Dev is a Python CLI tool that orchestrates Claude Code CLI sessions as an autonomous software development agency.

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
│  ┌──────────┐  ┌──────────┐                             │
│  │  State   │  │Workspace │                             │
│  │ Manager  │  │ Manager  │                             │
│  └──────────┘  └──────────┘                             │
└─────────────────────────────────────────────────────────┘
         │                │
         ▼                ▼
  .agentic-dev/     Claude Code CLI
  (state.json)      (subprocess)
```

## Module Responsibilities

### `cli.py`
User-facing CLI commands. Wires together all components and handles user interaction.

### `orchestrator/engine.py`
Finite state machine that drives the pipeline. Advances through phases, delegates to QA cycle and sprint runner, checks checkpoints.

### `orchestrator/qa_cycle.py`
Reusable pattern: action agent → QA agent → optional correction. Used by every team.

### `orchestrator/sprint_runner.py`
Executes a single sprint: backend → frontend → integration (if needed). Resolves working directories from the project's `DirectoryMap`.

### `orchestrator/adoption.py`
Orchestrates project adoption: runs `spec_reverse_engineer` agents (with QA cycles) in parallel for frontend/backend specs, then API contract, then feature extraction. Produces the full spec suite from existing code.

### `orchestrator/sync.py`
Orchestrates drift detection and resolution: runs `code_analyzer` agents to snapshot current state, `drift_detector` to compare against specs, and `spec_updater` to apply resolutions. Generates change requests for `to_code` items.

### `onboarding/structure_detector.py`
Detects project directory structure using a Claude agent. Scans for framework markers and proposes a frontend/backend directory mapping.

### `orchestrator/checkpoint.py`
Configurable pause points. Default: pause after design phase.

### `claude/runner.py`
Async subprocess wrapper for the `claude` CLI. Builds commands from agent configs. In print mode the rendered prompt is passed immediately after `-p`, before flags such as `--allowedTools`, so the CLI does not parse the prompt as an extra tool name.

### `agents/registry.py`
Loads agent definitions from YAML files. Provides lookup by name and team.

### `prompts/renderer.py`
Jinja2 template engine. Renders agent prompts with document content and constraints.

### `documents/store.py`
Reads and writes specification documents to the project workspace.

### `state/manager.py`
Persists pipeline state as JSON. Supports atomic writes and history archiving.

### `workspace/manager.py`
Creates and adopts project directories. Initializes git repos. Generates CLAUDE.md files. Resolves directory paths through `DirectoryMap` and the global project registry (`~/.agentic-dev/registry.json`).

### `mcp/claude_settings.py`
Discovers MCP servers from Claude Code's native settings files (`~/.claude/settings.json`, project `.claude/settings.json`, `.claude/settings.local.json`). Provides fuzzy matching to find servers by service name. Agents inherit configured MCP servers automatically — no `--mcp-config` flag needed.

### `onboarding/figma.py`
Figma-specific helpers. `analyze_figma_designs()` runs Claude agents with Figma MCP to extract design analyses. `write_figma_sources()` persists Figma URLs as the `figma_sources` doc. `run_design_diff()` invokes the `design_diff` agent to compare old vs new design analyses and produce a `design_changes` summary. `check_figma_mcp_available()` validates the Figma MCP server is configured in the Claude Code environment.

### `documents/diff.py`
Spec diffing helpers. `run_spec_diff()` invokes the `spec_diff` agent to compare old vs new structured input and produce a `spec_changes` summary. Used during `--full-spec` update cycles.

### `mcp/catalog.py`
Text-based service detection using regex patterns. Scans sprint plan text for references to known services (figma, github, stripe, supabase).

### `mcp/setup.py`
Rich-formatted prerequisite validation and guided setup helpers. Checks Claude Code settings for configured MCP servers and guides users to `claude mcp add` or Claude Code's OAuth UI.

### `config.py`
Global settings, constants, and project configuration models. Contains `ProjectConfig` (with `DirectoryMap`, `ExternalSource`, checkpoint config, sync ignores, `frontend_kind`, and `uat_mode`), config migration logic, and the global project registry.

### `uat/dispatcher.py`
Pure-logic module that maps `(ProjectType, FrontendKind, desktop_framework)` to a concrete UAT agent name (`uat_web`, `uat_cli`, `uat_desktop_electron`, `uat_desktop_tauri`, `uat_mobile`, `uat_api`). Invalid combinations raise `ValueError`. Also hosts `_read_desktop_framework()` which extracts the `desktop_framework` header from a `frontend_spec` text.

### `uat/prereqs.py`
Runtime prereq probes for each per-kind UAT agent. Checks that driver tools are not only on PATH but actually usable (e.g., `maestro --version` plus `maestro doctor`; `flutter --version` plus a booted non-web device). Emits `UATPrereqValidationEvent` and writes a structured `uat_prereqs` document the UAT agent reads before starting.

### `uat/validator.py`
Code-level enforcement of the false-PASS invariant. Parses the UAT report after the action agent completes and rewrites `Overall: PASS` to `FAIL` (prepending a `## Validator Override` section) when any of four structural rules fail: no runtime AC, runtime PASS without artifacts, all-`none` drivers with overall PASS in `uat_mode: full`, or any PASS AC lacking concrete `Evidence:` bullets.

## Data Flow

Text and design are parallel input channels that merge into `extra_context` flowing to all downstream agents.

1. User input → Input Processor → Structured Input (includes `## Project Type` and `## Frontend Kind`)
2. Figma URLs → Figma Analyzer → Design Analyses + Figma Sources (stored independently)
3. On update (`--full-spec`): old + new Structured Input → Spec Diff → Spec Changes
4. On update (`--from-figma`): old + new Design Analyses → Design Diff → Design Changes
5. Structured Input → Feature Analyst (+QA) → Features Request
6. Features Request → Architect (+QA; frontend_kind-aware) → Frontend Spec + Backend Spec + API Contract
7. All specs → Sprint Planner (+QA) → Sprint Plan
8. Per sprint: specs + sprint scope + `frontend_kind` + extra_context → Dev agents (+QA; kind-aware templates) → Code
   - Frontend agents also receive Figma Sources + `figma_mcp_available` for direct design access
9. Pre-UAT: prereq probes write `uat_prereqs` doc; artifacts directory created at `.agentic-dev/uat_artifacts/<run_id>/`
10. Per-kind UAT agent drives the running product → UAT Report → Validator (false-PASS gate) → UAT QA review

## FrontendKind axis

`FrontendKind` is orthogonal to `ProjectType`. `ProjectType` decides which specs exist (fullstack/frontend_only/backend_only); `FrontendKind` decides *how* the client is built and verified.

| FrontendKind | Developer guidance | UAT driver |
|---|---|---|
| `web` | Pages / components / routing | Playwright MCP |
| `cli` | Commands / flags / stdout-stderr contract / non-interactive mode | subprocess (Bash) |
| `desktop` (electron) | Windows, menus, IPC, packaging | Playwright attached via CDP |
| `desktop` (tauri) | Windows, menus, IPC, packaging | `tauri-driver` (WebDriver) |
| `mobile` | Screens, navigation, platform lifecycle | Maestro (fallback: project's own integration_test) |
| `none` | — | `uat_api` drives HTTP surface directly |

The detected kind lives on `PipelineState.frontend_kind` and `ProjectConfig.frontend_kind`. It can also be overridden at project creation time via `agentic-dev new --frontend-kind <kind>`.

## State Machine

```
New project pipeline:
IDLE → INPUT_PROCESSING → INPUT_PROCESSING_QA → FEATURE_ANALYSIS →
FEATURE_ANALYSIS_QA → ARCHITECTURE → ARCHITECTURE_QA → SPRINT_PLANNING →
SPRINT_PLANNING_QA → DESIGN_CHECKPOINT → SPRINTING →
UAT (prereq-probe → dispatch → runtime-drive → validator) → UAT_QA → COMPLETE

Adoption:
IDLE → ADOPTING → ADOPTED (or → INPUT_PROCESSING if --extend)

Sync:
COMPLETE/ADOPTED → SYNCING → COMPLETE/ADOPTED
```

Any phase can transition to FAILED. COMPLETE, ADOPTED, and FAILED are terminal states. COMPLETE and ADOPTED can transition to INPUT_PROCESSING (for updates), SYNCING (for sync), or FEATURE_ANALYSIS/ARCHITECTURE (for targeted updates).
