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
Executes a single sprint: backend → frontend → integration (if needed).

### `orchestrator/checkpoint.py`
Configurable pause points. Default: pause after design phase.

### `claude/runner.py`
Async subprocess wrapper for the `claude` CLI. Builds commands from agent configs.

### `agents/registry.py`
Loads agent definitions from YAML files. Provides lookup by name and team.

### `prompts/renderer.py`
Jinja2 template engine. Renders agent prompts with document content and constraints.

### `documents/store.py`
Reads and writes specification documents to the project workspace.

### `state/manager.py`
Persists pipeline state as JSON. Supports atomic writes and history archiving.

### `workspace/manager.py`
Creates project directories. Initializes git repos. Generates CLAUDE.md files.

## Data Flow

1. User input → Input Processor → Structured Input
2. Structured Input → Feature Analyst (+QA) → Features Request
3. Features Request → Architect (+QA) → Frontend Spec + Backend Spec + API Contract
4. All specs → Sprint Planner (+QA) → Sprint Plan
5. Per sprint: specs + sprint scope → Dev agents (+QA) → Code
6. All code → UAT → Report

## State Machine

```
IDLE → INPUT_PROCESSING → FEATURE_ANALYSIS → FEATURE_ANALYSIS_QA →
ARCHITECTURE → ARCHITECTURE_QA → SPRINT_PLANNING → SPRINT_PLANNING_QA →
DESIGN_CHECKPOINT → SPRINTING → UAT → COMPLETE
```

Any phase can transition to FAILED. COMPLETE and FAILED are terminal states.
