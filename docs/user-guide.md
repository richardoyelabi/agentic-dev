# User Guide

## Prerequisites

1. Python 3.12+
2. Claude Code CLI installed and authenticated (`claude --version` should work)
3. Install agentic-dev: `pip install -e ".[dev]"`

## Creating a New Project

```bash
agentic-dev new my-app
```

You'll be prompted to describe your application. Be as detailed as you want — the Input Processor will normalize and structure your requirements.

You can also specify preferences:
- Tech stack: "Use Next.js for frontend, Django REST Framework for backend"
- Database: "Use PostgreSQL with Supabase"
- Deployment: "Deploy frontend on Vercel, backend on AWS"
- UI/UX: "Minimalist design, dark mode support"

### Onboarding from Existing Sources

You can onboard existing codebases and Figma designs so the agency understands your current project before generating new specifications. Both flags are optional and additive — they enrich the agency's context but don't replace your requirements.

#### Annotation Syntax

Both `--from-codebase` and `--from-figma` support an optional annotation using `::` as a separator:

```
--from-codebase /path/to/project::Frontend React app
--from-figma "https://figma.com/file/abc::Admin dashboard"
```

The annotation helps the agency understand what each source represents. It splits on the first `::` only, so URLs or annotations containing `::` are handled safely. If you don't need an annotation, just pass the path or URL directly.

#### From an Existing Codebase

```bash
agentic-dev new my-app --from-codebase /path/to/existing/project
```

A read-only Claude agent analyzes the codebase (using only Read, Glob, and Grep tools) and produces a **Codebase Analysis** document containing:

- **Tech Stack** — detected frameworks, languages, and database
- **Architecture** — discovered routes/endpoints, data models, and UI components
- **Patterns & Conventions** — coding patterns, naming conventions, project structure
- **Dependencies** — key dependencies and their purposes
- **Notes** — anything notable that would help in planning changes

The detected tech stack is used as defaults when generating specifications. Your explicit preferences (e.g., "use PostgreSQL") always take priority over what is detected.

#### From Figma Designs

```bash
agentic-dev new my-app --from-figma "https://figma.com/file/..."
```

A Claude agent with Figma MCP tools extracts design information and produces a **Design Analysis** document containing:

- **Pages** — layout structure and components per page
- **Components** — purpose, variants, and configurable properties
- **Design Tokens** — color palette, typography, and spacing scale
- **Navigation** — navigation structure and user flows

**Prerequisite:** The Figma MCP server must be configured. Place your Figma MCP config at the project's `mcp_configs/figma.json`. See the [MCP server configuration docs](https://github.com/anthropics/claude-code/blob/main/docs/mcp.md) for setup instructions. The CLI will show an error with setup guidance if the config is missing.

Design analyses are passed to the Architect agent, which incorporates design tokens, component names, page layouts, and navigation flows into the Frontend Spec.

#### Combining Multiple Sources

Both flags are repeatable. You can onboard multiple codebases and Figma files in a single command — they are analyzed concurrently:

```bash
agentic-dev new my-app \
  --from-codebase /path/frontend::"Frontend React app" \
  --from-codebase /path/backend::"Backend API" \
  --from-figma "https://figma.com/file/abc::Main UI" \
  --from-figma "https://figma.com/file/xyz::Design system"
```

You can also combine onboarding sources with your own requirements. Describe what you want to build at the prompt, and the agency will merge your intent with the analysis of existing sources.

#### How Results Flow Through the Pipeline

1. Each source is analyzed concurrently by a dedicated Claude agent
2. Analysis results are appended to your requirements text with section headers (e.g., `## Source: Codebase - Frontend React app`)
3. The combined text is saved as `docs/user_input.md` and passed to the **Input Processor**, which merges detected tech stack, features, and patterns with your stated preferences
4. Figma analyses are additionally saved as `docs/design_analyses.md` and passed directly to the **Architect** for frontend specification

## The Design Phase

After submitting your requirements, the agency runs the design phase:

1. **Input Processor** normalizes your input
2. **Feature Analyst** expands it into detailed features (reviewed by QA)
3. **Architect** produces Frontend Spec, Backend Spec, and API Contract (reviewed by QA)
4. **Sprint Planner** breaks features into sprints (reviewed by QA)

By default, the pipeline **pauses after design** so you can review the documents at `~/projects/my-app/docs/`.

## Reviewing Documents

After the design phase pauses, review:
- `docs/features_request.md` — Are all features captured correctly?
- `docs/api_contract.md` — Does the API design make sense?
- `docs/frontend_spec.md` — Is the UI architecture right?
- `docs/backend_spec.md` — Are the data models correct?
- `docs/sprint_plan.md` — Is the sprint decomposition reasonable?

## Resuming the Pipeline

```bash
# Resume as-is
agentic-dev resume my-app

# Resume with feedback
agentic-dev resume my-app --feedback "Use Supabase instead of raw PostgreSQL"
```

## Monitoring Progress

```bash
agentic-dev status my-app
agentic-dev cost my-app
agentic-dev logs my-app --agent backend_developer --sprint 1
```

## Updating an Existing Project

```bash
# Targeted change
agentic-dev update my-app --change-request "Add dark mode to settings page"

# Full re-specification
agentic-dev update my-app --full-spec requirements-v2.txt
```

## Adopting an Existing Project

Point agentic-dev at any existing codebase and it will reverse-engineer the full spec suite, making the project a first-class citizen.

```bash
# Basic adoption
agentic-dev adopt /path/to/my-project

# With explicit directory mapping
agentic-dev adopt /path/to/my-project --frontend client --backend server

# With Figma designs
agentic-dev adopt /path/to/my-project --from-figma "https://figma.com/file/abc::Main UI"

# Adopt and extend with new requirements
agentic-dev adopt /path/to/my-project --extend "Add an admin dashboard"
```

Adoption creates `.agentic-dev/` and `docs/` in-place, detects the directory structure (or uses your explicit `--frontend`/`--backend` overrides), then runs specialized agents to produce `frontend_spec.md`, `backend_spec.md`, `api_contract.md`, `features.md`, and `structured_input.md`.

After adoption, you can use all standard commands (`update`, `resume`, `sync`, `status`, `cost`) on the adopted project.

When `--extend` is used, adoption feeds into the standard pipeline: the Input Processor receives the extracted specs plus your new requirements, and the pipeline pauses at the design checkpoint for review before building.

## Syncing Code and Specs

After any changes — whether you edited code manually, updated Figma designs, or modified specs — use `sync` to detect and resolve drift.

```bash
# Full interactive sync
agentic-dev sync my-app

# Code is truth — update specs to match code
agentic-dev sync my-app --from code

# Specs are truth — queue code changes to match specs
agentic-dev sync my-app --from specs

# Check a specific area
agentic-dev sync my-app --scope api

# Check-only mode (no changes, just report)
agentic-dev sync my-app --check
```

In interactive mode, each drift item is presented and you choose how to resolve it:
- **to_spec** — update the spec document to match code reality
- **to_code** — queue a code change (apply later with `agentic-dev update --from-sync`)
- **ignore** — mark as intentional divergence (won't appear again)
- **defer** — skip for now, will reappear on next sync

## Configuring Checkpoints

```bash
# Pause after every sprint
agentic-dev config my-app --checkpoints design,sprint

# Full autonomy (no pauses)
agentic-dev config my-app --autonomy full

# Maximum control (pause everywhere)
agentic-dev config my-app --autonomy maximum
```

## Cost Management

The agency tracks costs per agent run. Use `agentic-dev cost my-app` to see a breakdown. Each agent has a max budget in its YAML definition — the agent stops when its turn limit is exhausted.

## Troubleshooting

### Pipeline Failed

Check the error: `agentic-dev status my-app`

Common causes:
- Claude CLI not authenticated
- Budget exceeded (increase max_turns in agent YAML)
- Network issues

Resume after fixing: `agentic-dev resume my-app`

### Agent Output Issues

Check logs: `agentic-dev logs my-app --agent <agent-name>`

Logs contain the full rendered prompt and raw Claude CLI output for each agent run.
