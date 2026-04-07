# Agentic-Dev: Autonomous Software Development Agency — Design Spec

> **See also:** [Adopt & Sync Design Spec](2026-04-07-adopt-and-sync-design.md) — extends this design with `adopt` and `sync` commands for existing project onboarding and continuous synchronization (8 additional agents, 3 new pipeline phases).

## Overview

A Python CLI tool (`agentic-dev`) that orchestrates Claude Code CLI sessions to function as an autonomous software development agency. Takes a product description as input and produces complete applications — fullstack (frontend + backend), frontend-only (with optional BaaS), or backend-only (API/service/library) — including CI/CD pipelines.

## Architecture

### Platform & Tooling
- **Orchestrator:** Python CLI using Typer, Pydantic, Jinja2, Rich, PyYAML
- **Agents:** Claude Code CLI sessions, each defined by a YAML config + Jinja2 prompt template
- **Output:** Git repos for frontend and/or backend (based on project type), created in user-specified directories
- **State:** JSON-based pipeline state with atomic persistence and history

### Teams & Agents (14 total)

| Team | Agent | Model | Purpose |
|---|---|---|---|
| Design & Architecture | Input Processor | sonnet | Normalizes user input, separates features from preferences |
| | Feature Analyst | opus | Expands input into detailed Features Request |
| | Feature Analyst QA | opus | Reviews Features Request independently |
| | Architect | opus | Produces Frontend Spec, Backend Spec, API Contract |
| | Architect QA | opus | Reviews specs for consistency, no duplication |
| | Sprint Planner | sonnet | Breaks features into feature-based sprints |
| | Sprint Planner QA | opus | Reviews sprint decomposition |
| Frontend | Frontend Developer | sonnet | Implements frontend per sprint using superpowers; maintains README.md and ARCHITECTURE.md |
| | Frontend QA | opus | Reviews frontend code and documentation independently |
| Backend | Backend Developer | sonnet | Implements backend per sprint using superpowers; maintains README.md and ARCHITECTURE.md |
| | Backend QA | opus | Reviews backend code and documentation independently |
| Integration | Integration Agent | sonnet | Connects third-party services via MCPs |
| | Integration QA | opus | Reviews integration work |
| QA | UAT Agent | opus | User acceptance testing across full stack |

### Pipeline Flow

```
User Input
    │
    ▼
[Input Processor] → Structured Input (includes detected project type)
    │
    ▼
[Feature Analyst] → Features Request
    │
    ▼
[Feature Analyst QA] → feedback → [Feature Analyst correction]
    │
    ▼
[Architect] → specs (varies by project type: see Project Types below)
    │
    ▼
[Architect QA] → feedback → [Architect correction]
    │
    ▼
[Sprint Planner] → Sprint Plan
    │
    ▼
[Sprint Planner QA] → feedback → [Sprint Planner correction]
    │
    ▼
── CHECKPOINT (default: pause here for user review) ──
    │
    ▼
For each sprint (sequential):
    1. [Backend Dev] → code → [Backend QA] → feedback → [Backend Dev correction]
    2. [Frontend Dev] → code → [Frontend QA] → feedback → [Frontend Dev correction]
    3. [Integration] → config → [Integration QA] → feedback → [Integration correction]
       (only if sprint is flagged needs_integration in Sprint Plan)
    │
    ▼
After all sprints:
    [UAT Agent] → UAT Report → User
```

## Project Types

The system supports three project types, auto-detected by the Input Processor from the user's natural language description:

| Type | Detection Signal | Architecture Docs | Sprint Cycles | Code Dirs |
|---|---|---|---|---|
| `fullstack` | Default; describes both UI and custom backend | Frontend Spec + Backend Spec + API Contract | Backend → Frontend → Integration | `frontend/` + `backend/` |
| `frontend_only` | Mentions BaaS (Supabase, Firebase, Convex) or describes UI with no custom backend | Frontend Spec (with embedded Backend Services section) | Frontend only | `frontend/` |
| `backend_only` | Describes API, service, CLI, or library with no UI | Backend Spec + API Contract | Backend only | `backend/` |

**Auto-detection:** The Input Processor includes a `## Project Type` section in the Structured Input document. The engine parses this after input processing and creates the appropriate code directories. No CLI flag is needed.

**BaaS handling:** For `frontend_only` projects using a BaaS, the Frontend Spec includes a "Backend Services" section describing BaaS schema, security rules, and client SDK configuration. No separate Backend Spec is produced.

**API Contract rules:** The API Contract exists only when there is a backend (`fullstack` and `backend_only`). For `frontend_only`, no API Contract is produced. For `backend_only`, the API Contract describes the interface for external consumers.

**Template variable strategy:** Templates use `StrictUndefined` in Jinja2. All variables are always passed — absent documents use empty string `""`. Templates use truthy checks (`{% if api_contract %}`) to conditionally render sections.

## Document Taxonomy

| Document | Producer | Consumers | Purpose |
|---|---|---|---|
| Structured Input | Input Processor | Feature Analyst | Normalized requirements + separated preferences |
| Features Request | Feature Analyst | Architect, Sprint Planner | Detailed features with acceptance criteria |
| Frontend Spec | Architect | Frontend Dev, Frontend QA | UI components, routes, state, pages |
| Backend Spec | Architect | Backend Dev, Backend QA | Data models, services, business logic |
| API Contract | Architect | All dev agents, Integration | Endpoints, schemas, auth — single source of truth |
| Sprint Plan | Sprint Planner | Sprint Runner, all devs | Ordered feature-based sprints with scope |
| Integration Guide | Integration Agent | User, UAT | Third-party setup, MCP configs, manual steps |
| QA Reports | All QA agents | Corresponding action agents | Review feedback |
| UAT Report | UAT Agent | User | Final acceptance test results |

**No-duplication principle:** For fullstack projects, the API Contract is the single source of truth for the frontend/backend interface. Neither the Frontend Spec nor Backend Spec repeats endpoint details. For backend_only projects, the API Contract describes the external interface. For frontend_only projects, BaaS configuration lives exclusively in the Frontend Spec. The Sprint Plan references features by ID from the Features Request.

## QA Pattern

Every action agent in the pipeline has an independent QA counterpart, with two intentional exceptions: the Input Processor (normalization task, low risk) and the UAT Agent (reports directly to the user, who is the reviewer). The QA cycle is:

1. Run action agent with input documents → output
2. Run QA agent with **only** the input documents + output (no shared internal context)
3. **Correction loop** (up to `max_corrections` rounds, default 1):
   - If QA approved → done
   - Run action agent again with input + previous output + QA feedback → corrected output
   - Run QA agent again on corrected output → new report
4. Done. The user always sees QA feedback on the final version of the output.

QA evaluates against 6 criteria:
- Does it do what it's supposed to do?
- Does it work well?
- Is it reasonably well designed?
- Is it safe (no security vulnerabilities)?
- No obvious scaling issues?
- Is it documented? (Frontend/Backend QA only: README.md and ARCHITECTURE.md are present, accurate, and free of stale references)

## Sprint System

- Feature-based: each sprint = one feature or small group of related features
- Sequential sprints (each builds on prior work)
- Within a sprint: backend first (so API exists), then frontend, then integration if needed
- For `frontend_only`: only the frontend cycle runs; for `backend_only`: only the backend cycle runs
- Sprint scope is extracted from the Sprint Plan and passed to dev agents as focused context

## Checkpoint System

| Checkpoint | Default | Configurable |
|---|---|---|
| After design phase | ON | Yes |
| After each sprint | OFF | Yes |
| Before UAT | OFF | Yes |
| Full autonomy | OFF | `--autonomy full` disables all |

When paused, user can inject feedback on resume: `agentic-dev resume --feedback "..."`. Feedback is prepended to the next agent's context.

## State Management

Pipeline state persisted to `<project>/.agentic-dev/state.json`:
- Current phase (finite state machine)
- Sprint progress (per-sprint status)
- Agent session IDs (for resume capability)
- Cost tracking (per agent, per sprint)
- Error state

State saved atomically after every transition. Previous states archived with timestamps. Pipeline survives interruption — `agentic-dev resume` picks up exactly where it left off.

## Agent Definition Format

YAML files in `src/agentic_dev/agents/definitions/`:

```yaml
name: architect
description: "Produces Frontend Spec, Backend Spec, and API Contract"
team: design_architecture
claude:
  model: opus
  permission_mode: plan
  allowed_tools: [Read, Glob, Grep, WebSearch, WebFetch]
  max_budget_usd: 2.00
  use_bare_mode: true
prompt_template: architect.md.j2
input_documents: [features_request, structured_input]
output_documents: [frontend_spec, backend_spec, api_contract]
qa_agent: architect_qa
constraints:
  - "Prioritize minimalism: simplest solution that solves the problem"
  - "API contract is the single source of truth"
  - "No duplication across output documents"
```

## Prompt Templates

Jinja2 templates in `src/agentic_dev/prompts/templates/`:
- Each agent has a `.md.j2` template
- Templates inject: document content, sprint context, constraints
- Correction mode: adds previous output + QA feedback
- Partials for shared context blocks (API contract, sprint scope)
- Multi-document output uses `<!-- DOCUMENT: name -->` section markers

## CLI Interface

```
agentic-dev new <app-name> [--path] [--from-figma] [--from-codebase]
agentic-dev resume [<app-name>] [--feedback]
agentic-dev update <app-name> [--change-request | --full-spec]
agentic-dev status [<app-name>]
agentic-dev config <app-name> [--checkpoints] [--autonomy]
agentic-dev logs <app-name> [--agent] [--sprint]
agentic-dev cost <app-name>
```

## Update Support

Two modes:
1. **Full re-description:** User provides updated feature description. Agency diffs against existing spec to determine changes.
2. **Targeted change request:** User describes only the change. Agency determines scope and restarts from the appropriate pipeline phase.

Both modes reuse the same pipeline — the Input Processor receives update context alongside the change.

## Existing Project Onboarding

The `onboarding/` module handles:
- **Codebase analysis:** Detects tech stack, extracts routes/models/components, produces structured summary
- **Figma import:** Extracts pages, components, design tokens via MCP

Output feeds into the normal pipeline as additional context for the Input Processor.

## Generated Project Structure

```
~/projects/<app-name>/
├── .agentic-dev/          # Agency metadata (state, config, history, logs)
├── docs/                  # All spec documents and QA reports
├── frontend/              # Git repo (fullstack + frontend_only)
└── backend/               # Git repo (fullstack + backend_only)
```

Code directories are created after the Input Processor detects the project type. The CLI creates only the base structure (`.agentic-dev/` + `docs/`); the engine creates code directories post-detection.

Each generated repo gets a tailored `CLAUDE.md`, a `.github/workflows/ci.yml` for CI/CD, and developer-maintained documentation (`README.md` and `ARCHITECTURE.md`) that is updated incrementally each sprint.

**Generated CLAUDE.md content includes:**
- Project name and tech stack summary
- Coding conventions (e.g., "Use double quotes", "Functional components only")
- Testing framework and patterns
- API layer instructions (pointer to API Contract)
- CI/CD requirements
- Superpowers skill triggers:
  - "Always practise test-driven development" → triggers TDD skill
  - "Use the brainstorming skill before adding new components" → triggers brainstorming
  - "Use systematic debugging when tests fail" → triggers debugging skill
  - "Run verification before claiming work is complete" → triggers verification skill

The `workspace/claude_md.py` module generates this content from a Jinja2 template parameterized by tech stack preferences from the Structured Input.

## Dependencies

```
typer>=0.15       # CLI framework
pydantic>=2.0     # Data models and validation
jinja2>=3.1       # Prompt templates
rich>=13.0        # Terminal UI
pyyaml>=6.0       # Agent definition parsing
```

Dev: `pytest>=8.0`, `pytest-asyncio>=0.24`, `pytest-mock>=3.14`

## Agent Runner Mechanism

The Python orchestrator invokes Claude Code CLI via `asyncio.create_subprocess_exec`. Each agent run is a single `claude` CLI invocation in print mode (`-p`).

**Invocation pattern:** The user prompt must be the first argument after `-p`. `--allowedTools` accepts multiple values; if the prompt is placed after it, the CLI treats the prompt as another tool name and print mode fails with “no input”.

```bash
claude -p "$(cat rendered_prompt.md)" \
  --output-format json \
  --model <agent.model> \
  --permission-mode <agent.permission_mode> \
  --allowedTools <agent.allowed_tools> \
  --max-turns 50
```

Flags used:
- `-p` (print mode): non-interactive, reads prompt from the positional argument immediately following `-p`, outputs result
- `--output-format json`: returns structured JSON with `result`, `session_id`, `cost_usd`
- `--model`: `claude-opus-4-6` or `claude-sonnet-4-6`
- `--permission-mode`: `plan` (read-only, for design agents) or `bypassPermissions` (for dev agents that write code)
- `--allowedTools`: restricts tool access per agent definition
- `--max-turns`: limits agent iterations (budget control mechanism)

**Input passing:** All input documents are inlined into the rendered Jinja2 prompt template. The prompt is a single string containing role description, task instructions, and full document content. This avoids file-path dependencies and ensures the agent has everything it needs in context.

**Output capture:** The orchestrator reads JSON from stdout. For agents producing multiple documents (e.g., Architect), the output text contains section markers:
```
<!-- DOCUMENT: frontend_spec -->
...content...
<!-- DOCUMENT: backend_spec -->
...content...
<!-- DOCUMENT: api_contract -->
...content...
```
The `output_parser` module splits on `<!-- DOCUMENT: <name> -->` markers (where `<name>` matches the agent's `output_documents` list). If a marker is missing, the parser raises an error and the agent run is marked as failed.

**For code-producing agents** (Frontend Dev, Backend Dev): the agent writes directly to the filesystem in its working directory. The orchestrator does not capture code output from stdout — it's already on disk. The JSON output is used only for session tracking and cost.

**Session management:** Claude CLI returns a `session_id` in JSON output. The orchestrator stores this in state. However, session resume is not relied upon for correctness — if a session cannot be resumed, the orchestrator re-runs the agent from scratch with the same input. Sessions are an optimization, not a requirement.

**Cost tracking:** The `cost_usd` field from Claude CLI JSON output is recorded per agent run. The orchestrator sums costs in the pipeline state. Budget limits are enforced via `--max-turns` (the agent stops when turns are exhausted).

**"Superpowers"** refers to the Claude Code superpowers plugin — a set of skills (TDD, brainstorming, debugging, etc.) installed at `~/.claude/plugins/`. Dev agents use these skills via their CLAUDE.md instructions (e.g., "Use test-driven development" triggers the TDD skill). The plugin is already installed on the system.

## Document Schemas

Documents are markdown files with light structure. The orchestrator parses them where needed.

**Structured Input:**
```markdown
# Structured Input
## Project Type
<fullstack | frontend_only | backend_only>
## Feature Requirements
- [F001] <feature name>: <description>
- [F002] ...
## Preferences
### Tech Stack
- Frontend: <framework>
- Backend: <framework>
- Database: <database>
### Deployment
- Frontend: <service>
- Backend: <service>
### Plugins & Skills
- <plugin>: <usage>
### UI/UX Preferences
- <preference>
```

**Features Request:**
```markdown
# Features Request
## Feature: [F001] <name>
### Description
<detailed description>
### Acceptance Criteria
- [ ] <criterion 1>
- [ ] <criterion 2>
### Dependencies
- <dependency on other features, if any>
### Priority
<high/medium/low>
```

**API Contract:**
```markdown
# API Contract
## Authentication
<auth scheme>
## Endpoints
### [E001] <METHOD> <path>
- **Feature:** [F001]
- **Request:** <schema or example>
- **Response:** <schema or example>
- **Errors:** <error codes and meanings>
```

**Sprint Plan:**
```markdown
# Sprint Plan
## Sprint 1: <name>
- **Type:** new
- **Features:** [F001], [F002]
- **Dependencies:** none
- **Needs Integration:** yes/no
- **Integration Services:** <service names, if applicable>
## Sprint 2: <name>
- **Type:** new
- **Features:** [F003]
- **Dependencies:** Sprint 1
- **Needs Integration:** no
```

Sprint types: `new` (build from scratch), `patch` (modify existing code for update scenarios). Dev agents receive the sprint type and adjust their approach — `new` sprints create files, `patch` sprints modify existing files guided by the diff context.

**Frontend Spec:**
```markdown
# Frontend Spec
## Tech Stack
- Framework: <e.g. Next.js 15>
- State management: <e.g. TanStack Query>
- Styling: <e.g. Tailwind CSS v4>
- Testing: <e.g. Vitest + React Testing Library>
## Pages & Routes
### [P001] <page name> — <route path>
- **Features:** [F001]
- **Components:** <list of components on this page>
- **State:** <what state this page manages>
- **Behavior:** <interactions, transitions>
## Shared Components
### [C001] <component name>
- **Purpose:** <what it does>
- **Props:** <key props>
- **Used by:** [P001], [P003]
## State Management
- <global state shape and management approach>
## Authentication & Authorization
- <how auth is handled on the frontend — references API Contract for endpoints>
```

**Backend Spec:**
```markdown
# Backend Spec
## Tech Stack
- Framework: <e.g. Django REST Framework>
- Database: <e.g. PostgreSQL>
- Testing: <e.g. Pytest>
## Data Models
### [M001] <model name>
- **Features:** [F001]
- **Fields:** <field name: type, constraints>
- **Relationships:** <FK/M2M to other models>
## Services & Business Logic
### [S001] <service name>
- **Features:** [F001]
- **Purpose:** <what it does>
- **Inputs/Outputs:** <key interfaces>
## Background Jobs
### [J001] <job name> (if applicable)
- **Trigger:** <what triggers it>
- **Purpose:** <what it does>
## Infrastructure
- Database migrations strategy
- Environment variables required
- Third-party service credentials needed
```

Note: Neither Frontend Spec nor Backend Spec includes endpoint details — those live exclusively in the API Contract. The Frontend Spec references API Contract endpoint IDs where relevant. The Backend Spec describes the services that implement those endpoints.

The orchestrator parses Sprint Plan sections to iterate over sprints and determine integration needs.

## Update Flow

When the user runs `agentic-dev update`:

**Mode 1 — Targeted change request** (`--change-request "add dark mode to settings"`):
1. The Input Processor receives the change request + existing Structured Input
2. It produces an **updated** Structured Input with changes marked (new features get new IDs, modified features are flagged)
3. The Feature Analyst receives existing Features Request + updated Structured Input, produces updated Features Request
4. Normal QA cycles continue from Feature Analyst onward
5. The Architect receives the diff (old vs new Features Request) and updates only the affected specs
6. The Sprint Planner creates new sprints for only the changed/added features

**Mode 2 — Full re-description** (`--full-spec <file>`):
1. The Input Processor receives the new full description + existing Structured Input
2. It produces a new Structured Input, marking additions, modifications, and removals
3. Pipeline continues as in Mode 1

**Phase restart rules:**
- If only new features are added: restart from Feature Analyst
- If existing features are modified: restart from Feature Analyst (changes propagate through Architect and Sprint Planner)
- If the change is purely UI: restart from Architect (skip Feature Analyst, update Frontend Spec only)
- The Input Processor determines the scope and sets a `restart_from` field in the updated Structured Input

**Blast radius:** When the API Contract changes, both frontend and backend sprints for affected endpoints are regenerated. The Sprint Planner creates minimal "patch sprints" that target only the changed functionality.

## Existing Project Onboarding

Onboarding uses a dedicated Claude agent (not a Python module) to analyze the existing codebase.

**Codebase onboarding** (`--from-codebase <path>`):
1. The `onboarding_analyzer` agent is invoked with read-only access to the codebase
2. It produces a **Codebase Analysis** document:
   ```markdown
   # Codebase Analysis
   ## Tech Stack
   - Frontend: <detected framework, version>
   - Backend: <detected framework, version>
   - Database: <detected database>
   ## Architecture
   - <routes/endpoints found>
   - <data models found>
   - <UI components found>
   ## Patterns & Conventions
   - <coding patterns observed>
   ```
3. This document is passed to the Input Processor as additional context alongside user requirements
4. The Input Processor merges codebase reality with user intent into the Structured Input

**Figma onboarding** (`--from-figma <url>`):
1. Requires the Figma MCP server to be configured (the CLI checks and warns if missing with setup instructions)
2. The `onboarding_figma` agent is invoked with MCP config pointing to the Figma MCP server
3. It uses MCP tools (`get_file`, `get_components`, `get_styles`) to extract design information
4. Produces a **Design Analysis** document:
   ```markdown
   # Design Analysis
   ## Pages
   ### <page name>
   - **Layout:** <description of layout structure>
   - **Components:** <list of components used>
   ## Components
   ### <component name>
   - **Purpose:** <what it represents>
   - **Variants:** <variants if any>
   - **Props:** <configurable properties>
   ## Design Tokens
   - **Colors:** <color palette>
   - **Typography:** <font families, sizes, weights>
   - **Spacing:** <spacing scale>
   ```
5. This feeds into the Input Processor alongside other context
6. Design analyses are also stored as a separate `design_analyses` document and passed directly to the Architect, which incorporates design tokens, component names, page layouts, and navigation flows into the Frontend Spec

Both are optional and additive — they enrich the Input Processor's context but don't replace user requirements.

## Integration Agent Details

The Integration Agent connects third-party services that the application depends on. It operates differently from dev agents:

**Execution mechanism:**
- The Sprint Plan's `Integration Services` field lists service names (e.g., "Stripe", "Auth0", "SendGrid")
- The Integration Agent receives: the API Contract (to understand what integrations are needed), the service names, and the backend/frontend codebases
- It uses `bypassPermissions` mode to install SDKs, create config files, and write integration code
- For services with MCP servers available (e.g., Stripe, GitHub), the agent is invoked with the relevant MCP config so it can use service-specific tools
- For services without MCP support, the agent writes integration code using the service's SDK and produces a manual configuration guide

**MCP configuration:**
- The orchestrator maintains a mapping of known services to MCP server configs at `src/agentic_dev/agents/mcp_configs/`
- Example: `stripe.json`, `github.json`, `supabase.json`
- When a sprint needs integration with a known service, the orchestrator passes `--mcp-config <service>.json` to the Integration Agent
- For unknown services, the agent works without MCP tools and relies on WebSearch + SDK documentation

**Output:**
- Integration code written directly to the frontend/backend repos
- An Integration Guide document listing: what was configured automatically, what requires manual setup (API keys, webhook URLs, DNS records), and verification steps

## Permission Modes

Claude Code CLI permission modes used by agents:

| Mode | Used By | Effect |
|---|---|---|
| `plan` | Design agents (Input Processor, Feature Analyst, Architect, Sprint Planner, all QA, UAT) | Read-only: can read files but cannot create, edit, or execute. Safe for analysis and document production. |
| `bypassPermissions` | Dev agents (Frontend Dev, Backend Dev, Integration) | Full access: can read, write, execute commands. Required for code generation, running tests, installing packages. |

Design agents use `plan` mode because their output is captured from stdout (inlined in prompts). Dev agents use `bypassPermissions` because they write directly to the filesystem.

## Model Assignment Rationale

- **Opus** for agents requiring deep reasoning, critical review, or complex multi-document synthesis: Feature Analyst, Architect, all QA agents, UAT
- **Sonnet** for agents doing structured transformation, code generation, or mechanical decomposition: Input Processor, Sprint Planner, Frontend/Backend Dev, Integration. Sonnet is also more cost-effective for dev agents that run once per sprint.

## Context Window Management

For large projects, input documents may approach context limits. Mitigations:

- **Sprint scoping:** Dev agents receive only the sprint-relevant portions of specs, not the full documents. The prompt renderer extracts sections matching the sprint's feature IDs.
- **API Contract filtering:** Dev agents receive only the endpoints relevant to their current sprint's features.
- **Incremental context:** In update mode, agents receive diffs rather than full documents where possible.
- If a rendered prompt exceeds 80% of the model's context window, the orchestrator logs a warning. The user can break the sprint into smaller chunks via `agentic-dev config`.

## State Persistence Details

Atomic writes use write-to-temp-then-rename: the orchestrator writes `state.json.tmp`, then renames it to `state.json`. This ensures a crash during write cannot corrupt the state file.

Logs are stored at `.agentic-dev/logs/<agent-name>-sprint<N>-<timestamp>.log` and contain the full rendered prompt and raw Claude CLI JSON output for each agent run.

## Error Handling

- Agent failure (exit code, max-turns exhausted, unparseable output): state saved with error, user notified
- No automatic retries — user decides via `resume`
- State persistence survives any interruption
