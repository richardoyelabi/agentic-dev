# Agentic-Dev

Autonomous software development agency powered by Claude Code CLI.

agentic-dev is a **process enforcer**, not a project scaffolder. You run it
from inside an existing project the way you run `git` or `claude`. There is
no `new` command, no app-name registry, no `--path` flag. The directory you
invoke it from is the project; the tool's job is to enforce a deterministic
plan → architecture → sprints → QA → UAT pipeline.

## The pipeline

```
IDLE -> INPUT_PROCESSING -> FEATURE_ANALYSIS -> ARCHITECTURE -> SPRINT_PLANNING
     -> DESIGN_CHECKPOINT -> SPRINTING -> UAT -> COMPLETE
```

Every phase is deterministic and persisted to `.agentic-dev/state.json`. The
phases never change based on the project; only the agent outputs do.

## Primary command: `agentic-dev work`

```bash
cd /path/to/any/project
agentic-dev work "add a referrals feature"
```

`work` walks up from `cwd` looking for `.agentic-dev/`. If absent, it
scaffolds one in `cwd` and runs the pipeline from scratch. On subsequent
calls it dispatches on `state.phase`:

| Phase on entry | Behaviour |
|---|---|
| no `.agentic-dev/` | discover tracks → analyse existing code → scaffold → run pipeline |
| `COMPLETE` | enqueue the prompt as an update cycle |
| `FAILED` | inject the prompt as feedback and auto-resume |
| anything mid-pipeline | exit 1 — use `agentic-dev resume` |

Other supporting commands all operate on the cwd-resolved project (no
app-name argument):

- `agentic-dev resume` — continue a paused/failed pipeline; takes
  `--feedback` and `--skip-sprint`
- `agentic-dev remediate` — re-enter the pipeline using the last UAT
  report as the change request (the outer ralph loop)
- `agentic-dev tracks` — show inferred tracks; `--rediscover` re-runs the
  discovery agent and persists the result
- `agentic-dev status` / `config` / `logs` / `cost` — read-only or
  checkpoint-config helpers

## Track inference

A track is one codebase with a coherent build/run/test loop. Tracks are
inferred on first `work`:

1. If `agentic-dev.yaml` exists at the project root, its `tracks:` list
   is authoritative.
2. Otherwise, a Claude-driven `discovery_agent` (Read/Glob/Grep tools)
   walks the project and emits one track per detected codebase, choosing
   `kind` from the language/framework signals it finds and `uat_kind` to
   match when a runtime UAT is feasible.

`Track(name, path, kind, uat_kind)` is persisted to
`.agentic-dev/config.json`. Override at any time by editing that file or
adding an `agentic-dev.yaml`.

Existing code in each track is analysed by `analyze_codebase` in parallel
during onboarding; results land in `track_<name>_analysis.md` artifacts and
are concatenated into `existing_code_analyses.md`, which the architect reads
to reverse-engineer specs that reflect what's already there rather than
designing from scratch.

## Zero-config bootstrap

Onboarding writes `.agentic-dev/` and adds a managed `# >>> agentic-dev managed >>>`
block to the project's `.gitignore` so the metadata directory and
`.agentic-dev/secrets.env` are excluded from commits automatically (no-op
when the project is not a git repo). Removing the closing marker is treated
as an explicit opt-out and the block is never re-added.

A sibling onboarding step runs `detect_environment` alongside the analyser.
It reads root + per-track build manifests (Makefile, docker-compose*.yml,
package.json scripts, pyproject.toml, .env*, READMEs, scripts/) and produces
three cross-track artifacts:

- `.agentic-dev/artifacts/bootstrap.md` — canonical install/run/test/UAT
  commands per track, in preference order: docker compose > Makefile >
  package.json scripts > raw commands. Used by the engine (synchronous
  pre-install before each UAT run via `uat/preinstall.py`) and read by
  every UAT agent.
- `.agentic-dev/artifacts/env_requirements.md` — env vars classified as
  **auto** (deterministic safe defaults), **mock** (mock service shipped
  in-repo), or **human** (real credentials required).
- `.agentic-dev/secrets.env` — gitignored skeleton with auto/mock values
  pre-filled and human-required values written as `KEY=<FILL ME: hint>`
  placeholders.

Before UAT dispatches per-track agents, `uat/secrets_gate.py` parses
`secrets.env`. If any placeholders remain, the engine raises
`CheckpointPause(phase=UAT)` and `_display_checkpoint` tells the user
exactly which keys to fill. The gate also refuses to run if `secrets.env`
is not gitignored (defensive against the managed block being deleted).

## Artifact layout

All agent-produced artifacts live under `<project>/.agentic-dev/artifacts/`.

- `.agentic-dev/artifacts/<track>_spec.md` — per-track architecture spec
- `.agentic-dev/artifacts/api_contract.md` — emitted iff any track has `kind=api`
- `.agentic-dev/artifacts/sprint_plan.md` — sprint plan with `Tracks in scope:` lines
- `.agentic-dev/artifacts/track_<name>_analysis.md` — per-track existing-code analysis
- `.agentic-dev/artifacts/existing_code_analyses.md` — concatenated input for the architect
- `.agentic-dev/artifacts/bootstrap.md` — canonical install/run/test/UAT commands per track
- `.agentic-dev/artifacts/env_requirements.md` — env vars classified as auto/mock/human
- `.agentic-dev/artifacts/figma_sources.md` — Figma URLs and user-supplied labels
- `.agentic-dev/artifacts/qa/<name>.md` — per-step QA reports
- `.agentic-dev/artifacts/uat_report_<track>.md` — per-track UAT verdict
- `.agentic-dev/artifacts/uat_report.md` — aggregated multi-track UAT report
- `.agentic-dev/uat/<run_id>/evidence/<track>/...` — UAT screenshots, transcripts
- `.agentic-dev/uat/<run_id>/install_<track>.log` — synchronous pre-install logs
- `.agentic-dev/secrets.env` — gitignored secrets template (auto/mock pre-filled, human placeholders)
- `.agentic-dev/state.json` — pipeline state
- `.agentic-dev/config.json` — project config (tracks, checkpoint, autonomy)

## Ralph-loop semantics

There is no separate outer "ralph" loop. Two layered loops give
ralph-style persistence:

1. The QA-correction cycle inside `run_qa_cycle` retries the action agent
   against the QA agent's feedback up to a bounded number of corrections.
2. The unbounded `agentic-dev remediate` command re-enters the pipeline
   from feature analysis whenever UAT reports `FAIL`, increments
   `remediation_cycle`, and is intended to be run as many times as needed
   until UAT passes.

Treat `remediate` as the outer ralph loop and the per-agent QA correction
cycle as the inner one.

## Removed commands

`new`, `update`, `adopt`, `sync`, `integrate`, `--path`, `--track`, and the
global project-name registry were removed in the process-enforcer refactor.
For an existing codebase, just `cd` into it and run `agentic-dev work`.
Drift handling: edit specs in `.agentic-dev/artifacts/` and re-run the
pipeline.

Document archiving (the old `docs/archive/cycle_N/` and `docs/archive/update_*/`
directories) was removed along with the top-level `docs/` tree. The pipeline
overwrites artifacts in place; per-cycle history is available via
`.agentic-dev/history/state-*.json` snapshots and git on the project repo.

## Tech Stack

- Python 3.12+
- Typer (CLI), Pydantic (models), Jinja2 (templates), Rich (terminal UI), PyYAML (config)
- Pytest for testing (with pytest-asyncio for async tests)

## Conventions

- Use double quotes for strings
- Use Pydantic for all data models
- Use async/await for subprocess calls to Claude CLI
- Agent definitions are YAML files in `src/agentic_dev/agents/definitions/`
- Prompt templates are Jinja2 files in `src/agentic_dev/prompts/templates/`
- Tests mirror the src structure under `tests/`

## Running Tests

```bash
pytest
```
