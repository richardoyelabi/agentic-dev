# Agentic-Dev

Autonomous software development agency powered by Claude Code CLI.

## Architecture: tracks, not fixed roles

A project is a collection of **tracks**, where each track is one codebase
(`Track(name, path, kind, uat_kind)`). A track lives in its own nested
directory inside the project root. The default for a fresh project is a single
track named `app` at the repo root; multi-codebase projects declare each track
explicitly via repeatable `--track name::path::kind[::uat_kind]` flags on
`agentic-dev new`.

Pipeline phases:

```
IDLE -> INPUT_PROCESSING -> FEATURE_ANALYSIS -> ARCHITECTURE -> SPRINT_PLANNING
     -> DESIGN_CHECKPOINT -> SPRINTING -> UAT -> COMPLETE
```

The architecture phase produces one `<track>_spec` per declared track, plus an
`api_contract` only when at least one track has `kind == "api"`. The sprint
planner emits a `**Tracks in scope:**` line per sprint; the sprint runner
iterates those tracks, invoking a single generic `developer` + `qa` pair per
track with the track kind injected into the prompt. UAT iterates every
UAT-capable track (`track.uat_kind is not None`) and the
`uat/aggregator.py` helper combines the per-track verdicts into the final
report.

The minimum useful invocation is `agentic-dev new myapp` (one default track).
The maximum is N nested directories with separate kinds, e.g.:

```
agentic-dev new shop \
  --track web::web::web::web \
  --track api::api::api::api \
  --track worker::workers/jobs::worker
```

## Artifact layout

All agent-produced artifacts live under `<project>/.agentic-dev/artifacts/`.
There is no top-level `docs/` directory. The relevant subpaths:

- `.agentic-dev/artifacts/<track>_spec.md` — per-track architecture spec
- `.agentic-dev/artifacts/api_contract.md` — emitted iff any track has `kind=api`
- `.agentic-dev/artifacts/sprint_plan.md` — sprint plan with `Tracks in scope` lines
- `.agentic-dev/artifacts/qa/<name>.md` — per-step QA reports
- `.agentic-dev/artifacts/uat_report_<track>.md` — per-track UAT verdict
- `.agentic-dev/artifacts/uat_report.md` — aggregated multi-track UAT report
- `.agentic-dev/uat/<run_id>/evidence/<track>/...` — UAT screenshots, transcripts
- `.agentic-dev/state.json` — pipeline state
- `.agentic-dev/config.json` — project config (tracks, checkpoint, etc.)

## Ralph-loop semantics

There is no separate outer "ralph" loop. Two layered loops already give
ralph-style persistence:

1. The QA-correction cycle inside `run_qa_cycle` retries the action agent
   against the QA agent's feedback up to a bounded number of corrections.
2. The unbounded `agentic-dev remediate <app>` command re-enters the pipeline
   from feature analysis whenever UAT reports `FAIL`, increments
   `remediation_cycle`, and is intended to be run as many times as needed
   until UAT passes.

Treat `remediate` as the outer ralph loop and the per-agent QA correction
cycle as the inner one.

## Removed commands

`adopt`, `sync`, and `integrate` have been removed. They existed only to
reconcile the previous frontend/backend/docs split, which no longer exists.
For an existing codebase, point `agentic-dev new` at it via `--path` and
declare the tracks with `--track`. For drift handling, edit specs in
`.agentic-dev/artifacts/` and re-run the pipeline.

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

