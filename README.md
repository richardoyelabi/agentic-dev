# Agentic-Dev

Autonomous software development agency powered by Claude Code CLI. Takes a product description and produces complete web/mobile applications with frontend and backend.

## Features

- **25 specialized agents** organized into 7 teams (Design & Architecture, Adoption, Sync, Frontend, Backend, Integration, QA)
- **Independent QA review** at every stage with one-cycle correction
- **Feature-based sprints** break large projects into manageable chunks
- **Configurable checkpoints** for human review (default: pause after design)
- **Full autonomy mode** for end-to-end unattended execution
- **Update support** for targeted changes, full re-specifications, and design updates via `--from-figma`
- **Figma as first-class design input** — design and text are parallel, equal channels; frontend agents get direct Figma MCP access during implementation
- **Automatic design and spec diffing** — `design_diff` and `spec_diff` agents produce change summaries that flow to all downstream agents
- **Adopt existing projects** with full spec reverse-engineering from codebases and Figma designs
- **Continuous sync** between code, specs, and Figma with flexible source-of-truth resolution

## Installation

```bash
pip install -e ".[dev]"
```

Requires:
- Python 3.12+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated

## Quick Start

```bash
# Create a new project
agentic-dev new my-saas-app

# Check status
agentic-dev status my-saas-app

# Resume after reviewing design documents
agentic-dev resume my-saas-app

# Resume with feedback
agentic-dev resume my-saas-app --feedback "Use Supabase instead of raw PostgreSQL"
```

## CLI Reference

> **Tip:** Always quote URLs and multi-word option values to prevent shell interpretation (e.g., `--from-figma "https://..."`, `--extend "add dark mode"`). See the [User Guide troubleshooting section](docs/user-guide.md#shell-quoting) for details.

### `agentic-dev new <app-name>`

Create a new project and start the development pipeline.

```
Options:
  --path TEXT           Directory to create the project in (default: ~/projects)
  --from-file TEXT      Path to a file containing project requirements
  --from-figma TEXT     Figma URL to import designs from (supports value::annotation, repeatable)
  --from-codebase TEXT  Path to existing codebase to use as reference context (supports value::annotation, repeatable)
```

`--from-codebase` and `--from-figma` analyze existing sources read-only and use them as context for the new project — they do not manage or modify the existing codebase. To bring an existing project under agentic-dev management in-place, use `adopt` instead.

See the [User Guide](docs/user-guide.md#referencing-existing-sources) for detailed documentation.

### `agentic-dev resume [app-name]`

Resume a paused or failed pipeline.

```
Options:
  --feedback TEXT  Feedback to inject into the next agent's context
```

### `agentic-dev adopt <path>`

Bring an existing project under agentic-dev management. Works **in-place** — no new directory is created. Specialized agents read the existing code and reverse-engineer the full spec suite (`frontend_spec.md`, `backend_spec.md`, `api_contract.md`, `features.md`). After adoption, all standard commands (`update`, `sync`, `status`, `cost`) work on the project.

This is different from `new --from-codebase`, which creates a **separate new project** using existing code only as reference context.

```
Options:
  --from-figma TEXT     Figma URL (supports value::annotation, repeatable)
  --extend TEXT         New requirements to add on top
  --frontend TEXT       Explicit frontend directory name
  --backend TEXT        Explicit backend directory name
  --yes / -y            Skip confirmation prompts
```

See the [User Guide](docs/user-guide.md#adopting-an-existing-project) for detailed documentation.

### `agentic-dev sync [app-name]`

Detect and resolve drift between code and specs — for example, after manual code edits or direct spec modifications. **Diagnostic**: compares current code state against specs, reports what's misaligned, and lets you choose how to resolve each item (update the spec, queue a code change, or ignore).

Use `update` instead when you want to request intentional new features or changes.

```
Options:
  --from TEXT           Source of truth: code, specs, or figma
  --scope TEXT          Sync scope: api, frontend, or backend
  --check               Check-only mode (report drift, no changes)
```

See the [User Guide](docs/user-guide.md#syncing-code-and-specs) for detailed documentation.

### `agentic-dev update <app-name>`

Request **intentional changes** to an existing project — adding features, modifying behavior, or replacing requirements entirely. Re-runs the pipeline from the appropriate phase. Requires the project to be in `COMPLETE` state.

Use `sync` instead if you've edited code or specs manually and need to resolve the resulting drift.

When neither `--from-file` nor `--full-spec` is provided, you'll be prompted to type or paste your change description interactively.

```
Options:
  --from-file TEXT   Path to a file containing change requirements
  --full-spec TEXT   Path to full updated spec file (triggers structured diff for optimal restart point)
  --from-figma TEXT  Figma URL to import updated designs from (supports value::annotation, repeatable)
```

`--from-file` and `--full-spec` are mutually exclusive. `--from-figma` is compatible with all options — it adds a parallel design channel alongside text changes. When Figma is provided, the pipeline automatically diffs old vs new design analyses and distributes the change summary to all downstream agents.

### `agentic-dev status [app-name]`

Show pipeline status: current phase, sprint progress, and costs.

### `agentic-dev config <app-name>`

Configure checkpoint behavior.

```
Options:
  --checkpoints TEXT  Comma-separated checkpoint names (design, sprint, uat)
  --autonomy TEXT     Autonomy level: full, default, or maximum
```

### `agentic-dev logs <app-name>`

View agent run logs.

```
Options:
  --agent TEXT   Filter by agent name
  --sprint INT   Filter by sprint number
```

### `agentic-dev cost <app-name>`

Show cost breakdown by agent and sprint.

## Architecture

The agency consists of 7 teams with 25 agents:

| Team | Agents | Purpose |
|---|---|---|
| Design & Architecture | Input Processor, Input Updater, Feature Analyst + QA, Architect + QA, Sprint Planner + QA, Design Diff, Spec Diff | Requirements analysis, specifications, sprint planning, change diffing |
| Adoption | Structure Detector, Spec Reverse Engineer + QA, Feature Extractor + QA | Reverse-engineer specs from existing codebases |
| Sync | Code Analyzer, Drift Detector, Spec Updater | Detect and resolve drift between code and specs |
| Frontend | Frontend Developer + QA | UI implementation per sprint (with direct Figma MCP access) |
| Backend | Backend Developer + QA | API and business logic per sprint |
| Integration | Integration Agent + QA | Third-party service connections |
| QA | UAT Agent | User acceptance testing |

### Pipeline Flow

```
New project:    Text + Figma → Design Phase → [CHECKPOINT] → Sprint 1..N → UAT → Done
Adopt project:  Existing Code → Spec Generation → [ADOPTED] → (update/sync as needed)
Adopt + extend: Existing Code → Spec Generation → Design Phase → Sprint 1..N → UAT → Done
Update:         Text changes + Figma changes (parallel) → Diff → Design Phase → Sprint 1..N → Done
Sync:           Code + Specs → Drift Detection → User Resolution → Spec/Code Updates
```

Text and design are **parallel input channels** — both flow independently through the pipeline. When Figma designs change, `design_diff` computes what changed; when full specs change, `spec_diff` computes what changed. Both summaries flow to every downstream agent including UAT.

Each sprint: Backend → Frontend (with Figma MCP access) → Integration (if needed)

### QA Pattern

Every agent has an independent QA counterpart. QA receives only the agent's input and output (no shared context). One review cycle: QA gives feedback → agent corrects → done.

### Documents Produced

- **Structured Input** — Normalized requirements
- **Features Request** — Detailed features with acceptance criteria
- **Frontend Spec** — UI components, routes, state management
- **Backend Spec** — Data models, services, business logic
- **API Contract** — Single source of truth for frontend/backend interface
- **Sprint Plan** — Feature-based sprint decomposition
- **Design Analyses** — Extracted pages, components, tokens, and navigation from Figma
- **Figma Sources** — Figma URLs passed to frontend agents for direct MCP access
- **Design Changes** — Summary of what changed between old and new design analyses (`design_diff`)
- **Spec Changes** — Summary of what changed between old and new full specs (`spec_diff`)
- **QA Reports** — Review feedback at every stage
- **UAT Report** — Final acceptance test results

## Configuration

Projects are created at `~/projects/<app-name>/` by default. The project structure:

```
~/projects/<app-name>/
├── .agentic-dev/     # Pipeline state, config, logs
├── docs/             # All specification documents
├── frontend/         # Separate git repo
└── backend/          # Separate git repo
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/ tests/

# Type check
mypy src/agentic_dev/ --ignore-missing-imports
```

## Design Spec

See [docs/superpowers/specs/2026-03-28-agentic-dev-agency-design.md](docs/superpowers/specs/2026-03-28-agentic-dev-agency-design.md) for the full design specification.
