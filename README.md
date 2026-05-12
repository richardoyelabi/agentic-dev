# Agentic-Dev

Autonomous software development agency powered by the Claude Code CLI. Takes a
product description, decomposes it into manageable sprints, drives the
implementation end-to-end, and runtime-verifies the result with UAT — across
any combination of web, CLI, desktop, mobile, API, and worker codebases.

## Core ideas

- **Tracks, not fixed roles.** A project is a list of `Track(name, path, kind, uat_kind)` values. Each track is one codebase in a nested directory inside the project root. The default is a single `app` track at the repo root; multi-codebase projects declare each track explicitly with repeatable `--track name::path::kind[::uat_kind]` flags.
- **Deterministic pipeline.** A finite state machine advances through `INPUT_PROCESSING → FEATURE_ANALYSIS → ARCHITECTURE → SPRINT_PLANNING → DESIGN_CHECKPOINT → SPRINTING → UAT → COMPLETE`. State is persisted to `.agentic-dev/state.json` and every step is resumable.
- **Per-track parallel artifacts.** The architect emits one `<track>_spec.md` per track (plus an `api_contract.md` only when any track has `kind=api`). The sprint planner produces a per-sprint `**Tracks in scope:**` line. The sprint runner runs one generic `developer` + `qa` cycle per in-scope track, passing the track's kind into the prompt for kind-specific guidance.
- **Multi-track UAT with aggregator.** Every track that has `uat_kind` set gets its own UAT cycle (web / api / cli / mobile / desktop). Per-track verdicts roll up through `uat/aggregator.py` into a single `## Overall Result: PASS|FAIL` report.
- **Ralph loop semantics.** Two layered loops give ralph-style persistence without an extra outer controller: the per-agent QA correction loop inside `run_qa_cycle`, and the unbounded `agentic-dev remediate` command which re-enters the pipeline whenever UAT reports `FAIL`.
- **All artifacts under `.agentic-dev/`.** No `docs/` directory is created. The codebases and their inline `README.md` / `ARCHITECTURE.md` are the only user-facing documentation.

## Project layout produced by the agency

```
<project>/
├── .agentic-dev/
│   ├── state.json
│   ├── config.json
│   ├── artifacts/                       # git-tracked agent artifacts
│   │   ├── user_input.md
│   │   ├── structured_input.md
│   │   ├── features.md
│   │   ├── <track>_spec.md              # one per track
│   │   ├── api_contract.md              # iff any track has kind=api
│   │   ├── sprint_plan.md
│   │   ├── sprint_<N>_<track>.md
│   │   ├── sprint_rolling_summary.md
│   │   ├── qa/<name>.md                 # QA reports
│   │   ├── uat_report_<track>.md
│   │   └── uat_report.md                # aggregated multi-track verdict
│   ├── uat/<run_id>/evidence/<track>/   # screenshots, transcripts, http logs
│   ├── logs/   sessions/   history/   runs/   agent_dumps/
└── <track_dirs>/                        # one nested dir per declared track
```

## Installation

```bash
pip install -e ".[dev]"
```

Requires:

- Python 3.12+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated

## Quick start

```bash
# Single-track project (defaults to one track named "app" at the repo root)
agentic-dev new my-saas-app

# Multi-track project: web frontend + JSON API + background worker
agentic-dev new shop \
  --track web::web::web::web \
  --track api::api::api::api \
  --track worker::workers/jobs::worker

# Check status
agentic-dev status my-saas-app

# Resume after reviewing artifacts at .agentic-dev/artifacts/
agentic-dev resume my-saas-app

# Resume with feedback
agentic-dev resume my-saas-app --feedback "Use Supabase instead of raw PostgreSQL"

# Remediate failing UAT (unbounded outer ralph loop)
agentic-dev remediate my-saas-app
```

## CLI reference

### `new <app-name>`

Create a new project and start the development pipeline.

```
Options:
  --path TEXT            Directory to create the project in (default: ~/projects)
  --from-file TEXT       Path to a file containing project requirements
  --from-figma TEXT      Figma URL to import designs (value::annotation, repeatable)
  --from-codebase TEXT   Existing codebase to use as reference context (value::annotation, repeatable)
  --track TEXT           Declare a codebase track: name[::path[::kind[::uat_kind]]]
                         Repeatable. Omit for a single default track at the repo root.
```

`--track` examples:

- `--track app` — track named `app` at `./app/`, kind=`generic`
- `--track web::frontend::web::web` — track named `web` at `./frontend/`, kind=`web`, UAT via `uat_web`
- `--track api::services/api::api::api` — track named `api` at `./services/api/`, kind=`api`, UAT via `uat_api`

`--from-codebase` and `--from-figma` analyze sources read-only as context. They never adopt or modify the source.

### `resume [app-name]`

Resume a paused or failed pipeline.

```
Options:
  --feedback TEXT  Feedback to inject into the next agent's context
```

### `update <app-name>`

Request intentional changes to a `COMPLETE` project. Re-runs the pipeline from the appropriate phase.

```
Options:
  --from-file TEXT   Path to a file containing change requirements
  --from-figma TEXT  Figma URL with updated designs (value::annotation, repeatable)
```

### `remediate <app-name>`

Fix failing UAT acceptance criteria by re-entering the pipeline with the UAT report as a remediation prompt. Increments `remediation_cycle`. Intended to be run as many times as needed until UAT passes.

### `status [app-name]`

Show pipeline status: current phase, sprint progress, and costs.

### `config <app-name>`

Configure checkpoint behavior and autonomy level.

### `logs <app-name>`

View per-run and per-agent logs from `.agentic-dev/logs/`.

### `cost <app-name>`

Show cost breakdown by agent and sprint.

## Tech stack

- Python 3.12+
- Typer (CLI), Pydantic (models), Jinja2 (templates), Rich (terminal UI), PyYAML (config)
- Pytest with pytest-asyncio for async tests

## Running tests

```bash
pytest
```

## License

See [LICENSE](LICENSE).
