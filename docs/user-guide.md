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

### From an Existing Codebase

```bash
agentic-dev new my-app --from-codebase /path/to/existing/project
```

The agency will analyze the codebase and produce specifications that match the existing architecture.

### From Figma Designs

```bash
agentic-dev new my-app --from-figma "https://figma.com/file/..."
```

Requires Figma MCP server configuration.

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
