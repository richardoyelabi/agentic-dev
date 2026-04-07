# Agentic-Dev

Autonomous software development agency powered by Claude Code CLI. Takes a product description and produces complete web/mobile applications with frontend and backend.

## Features

- **14 specialized agents** organized into 5 teams (Design & Architecture, Frontend, Backend, Integration, QA)
- **Independent QA review** at every stage with one-cycle correction
- **Feature-based sprints** break large projects into manageable chunks
- **Configurable checkpoints** for human review (default: pause after design)
- **Full autonomy mode** for end-to-end unattended execution
- **Update support** for both targeted changes and full re-specifications
- **Existing project onboarding** from codebases and Figma designs

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

### `agentic-dev new <app-name>`

Create a new project and start the development pipeline.

```
Options:
  --path TEXT           Directory to create the project in (default: ~/projects)
  --from-figma TEXT     Figma URL to import designs from (supports value::annotation, repeatable)
  --from-codebase TEXT  Path to existing codebase to onboard (supports value::annotation, repeatable)
```

See the [User Guide](docs/user-guide.md#onboarding-from-existing-sources) for detailed onboarding documentation.

### `agentic-dev resume [app-name]`

Resume a paused or failed pipeline.

```
Options:
  --feedback TEXT  Feedback to inject into the next agent's context
```

### `agentic-dev adopt <path>`

Adopt an existing project and reverse-engineer full specifications.

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

Detect drift between code, specs, and Figma designs, and resolve interactively.

```
Options:
  --from TEXT           Source of truth: code, specs, or figma
  --scope TEXT          Sync scope: api, frontend, or backend
  --check               Check-only mode (report drift, no changes)
```

See the [User Guide](docs/user-guide.md#syncing-code-and-specs) for detailed documentation.

### `agentic-dev update <app-name>`

Trigger an update cycle on an existing project.

```
Options:
  --change-request TEXT  Targeted change description
  --full-spec TEXT       Path to full updated spec file
```

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

The agency consists of 5 teams with 14 agents:

| Team | Agents | Purpose |
|---|---|---|
| Design & Architecture | Input Processor, Feature Analyst + QA, Architect + QA, Sprint Planner + QA | Requirements analysis, specifications, sprint planning |
| Frontend | Frontend Developer + QA | UI implementation per sprint |
| Backend | Backend Developer + QA | API and business logic per sprint |
| Integration | Integration Agent + QA | Third-party service connections |
| QA | UAT Agent | User acceptance testing |

### Pipeline Flow

```
User Input → Design Phase → [CHECKPOINT] → Sprint 1..N → UAT → Done
```

Each sprint: Backend → Frontend → Integration (if needed)

### QA Pattern

Every agent has an independent QA counterpart. QA receives only the agent's input and output (no shared context). One review cycle: QA gives feedback → agent corrects → done.

### Documents Produced

- **Structured Input** — Normalized requirements
- **Features Request** — Detailed features with acceptance criteria
- **Frontend Spec** — UI components, routes, state management
- **Backend Spec** — Data models, services, business logic
- **API Contract** — Single source of truth for frontend/backend interface
- **Sprint Plan** — Feature-based sprint decomposition
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
