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

### `mcp/catalog.py`
Text-based service detection using regex patterns. Scans sprint plan text for references to known services (figma, github, stripe, supabase).

### `mcp/setup.py`
Rich-formatted prerequisite validation and guided setup helpers. Checks Claude Code settings for configured MCP servers and guides users to `claude mcp add` or Claude Code's OAuth UI.

### `config.py`
Global settings, constants, and project configuration models. Contains `ProjectConfig` (with `DirectoryMap`, `ExternalSource`, checkpoint config, sync ignores), config migration logic, and the global project registry.

## Data Flow

1. User input → Input Processor → Structured Input
2. Structured Input → Feature Analyst (+QA) → Features Request
3. Features Request → Architect (+QA) → Frontend Spec + Backend Spec + API Contract
4. All specs → Sprint Planner (+QA) → Sprint Plan
5. Per sprint: specs + sprint scope → Dev agents (+QA) → Code
6. All code → UAT → Report

## State Machine

```
New project pipeline:
IDLE → INPUT_PROCESSING → FEATURE_ANALYSIS → FEATURE_ANALYSIS_QA →
ARCHITECTURE → ARCHITECTURE_QA → SPRINT_PLANNING → SPRINT_PLANNING_QA →
DESIGN_CHECKPOINT → SPRINTING → UAT → COMPLETE

Adoption:
IDLE → ADOPTING → ADOPTED (or → INPUT_PROCESSING if --extend)

Sync:
COMPLETE/ADOPTED → SYNCING → COMPLETE/ADOPTED
```

Any phase can transition to FAILED. COMPLETE, ADOPTED, and FAILED are terminal states. COMPLETE and ADOPTED can transition to INPUT_PROCESSING (for updates), SYNCING (for sync), or FEATURE_ANALYSIS/ARCHITECTURE (for targeted updates).
