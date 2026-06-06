# Agentic-Dev

Autonomous software development agency powered by the Claude Code CLI.
`agentic-dev` is a **process enforcer**, not a project scaffolder: you run
it from inside an existing project the way you run `git` or `claude`. It
decomposes a request into a deterministic plan → architecture → sprints →
QA → UAT pipeline, drives the implementation end-to-end across any
combination of web, CLI, desktop, mobile, API, and worker codebases, and
runtime-verifies the result.

## Core ideas

- **Process enforcer.** There is no `new` command, no app-name registry,
  and no `--path` flag. The directory you invoke `agentic-dev` from is the
  project. On first run it scaffolds a `.agentic-dev/` metadata directory
  in place; on subsequent runs it dispatches on persisted state.
- **Inferred tracks.** A track is one codebase with a coherent
  build/run/test loop. Tracks are discovered automatically on first run by
  a Claude agent that inspects the project. Drop an `agentic-dev.yaml` at
  the project root to override the discovery output explicitly.
- **Deterministic pipeline.** A finite state machine advances through
  `IDLE → INPUT_PROCESSING → FEATURE_ANALYSIS → ARCHITECTURE →
  SPRINT_PLANNING → DESIGN_CHECKPOINT → SPRINTING → UAT → COMPLETE`. Each
  agent phase is paired with a `_QA` phase. State persists to
  `.agentic-dev/state.json` and every step is resumable.
- **Per-track parallel artifacts.** The architect emits one
  `<track>_spec.md` per track, plus an `api_contract.md` when any track has
  `kind=api`. The sprint planner emits a per-sprint `**Tracks in scope:**`
  line. The sprint runner runs a generic `developer` + `qa` cycle per
  in-scope track, passing the track's `kind` into the prompt for
  kind-specific guidance.
- **Multi-track runtime UAT.** Every track with `uat_kind` set gets its
  own UAT cycle (`uat_web`, `uat_api`, `uat_cli`, `uat_mobile`,
  `uat_desktop_electron`, `uat_desktop_tauri`). Per-track verdicts roll up
  through `uat/aggregator.py` into a single
  `## Overall Result: PASS|FAIL` report. UAT agents drive long-running
  processes in the background and poll them with the `Monitor` tool. When
  Figma designs are available, the UI UAT agents also check design
  fidelity — comparing the running screens against the Figma frames and
  failing on material visual deviations.
- **Ralph-loop semantics.** Two layered loops give ralph-style persistence
  without an extra outer controller: the per-agent QA correction loop
  inside `run_qa_cycle`, and the unbounded `agentic-dev remediate`
  command which re-enters the pipeline whenever UAT reports `FAIL`.
- **All artifacts under `.agentic-dev/`.** No top-level `docs/` directory
  is created in the host project. The codebases and their inline
  `README.md` / `ARCHITECTURE.md` are the only user-facing documentation.

## Project layout produced by the agency

```
<project>/
├── .agentic-dev/
│   ├── state.json
│   ├── config.json
│   ├── artifacts/                       # agent artifacts
│   │   ├── structured_input.md
│   │   ├── features.md
│   │   ├── <track>_spec.md              # one per track
│   │   ├── api_contract.md              # iff any track has kind=api
│   │   ├── sprint_plan.md
│   │   ├── sprint_<N>_<track>.md
│   │   ├── track_<name>_analysis.md     # per-track existing-code analysis
│   │   ├── existing_code_analyses.md    # concatenated input for the architect
│   │   ├── figma_sources.md
│   │   ├── qa/<name>.md                 # per-step QA reports
│   │   ├── uat_prereqs_<track>.md
│   │   ├── uat_report_<track>.md
│   │   └── uat_report.md                # aggregated multi-track verdict
│   ├── uat/<run_id>/evidence/<track>/   # screenshots, transcripts, http logs
│   ├── history/                         # state snapshots (state-*.json)
│   ├── logs/   sessions/   runs/   agent_dumps/
└── <track_dirs>/                        # one nested dir per discovered track
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
# Run agentic-dev against the project containing the current directory.
# First call onboards (discovery → analysis → scaffold → pipeline);
# subsequent calls dispatch on state (COMPLETE = update, FAILED = resume).
cd /path/to/my/project
agentic-dev work "add a referrals feature where users earn credits"

# Check status
agentic-dev status

# Resume after a checkpoint (or after fixing a FAILED phase)
agentic-dev resume

# Resume with feedback injected
agentic-dev resume --feedback "Use Supabase instead of raw PostgreSQL"

# Remediate failing UAT (unbounded outer ralph loop)
agentic-dev remediate
```

To override the auto-inferred tracks, commit an `agentic-dev.yaml` at the
project root:

```yaml
# agentic-dev.yaml
tracks:
  - name: backend
    path: backend
    kind: api
    uat_kind: api
  - name: frontend
    path: frontend
    kind: web
    uat_kind: web
```

`kind` and `uat_kind` are free-form strings. Common values: `web`, `api`,
`cli`, `worker`, `desktop`, `mobile`, `library`, `generic`. Set `uat_kind`
when a runtime UAT is feasible; leave it null otherwise.

## CLI reference

All commands operate on the cwd-resolved project — there is no `<app-name>`
argument. `agentic-dev` walks upward from `cwd` looking for a
`.agentic-dev/` directory the way `git` walks for `.git/`.

### `work [prompt]`

Primary entry point. Onboards on first run, dispatches on state on
subsequent runs.

```
Options:
  prompt              What you'd like agentic-dev to do (positional;
                      omit to provide via --from-file or stdin)
  --from-file TEXT    Read the work request from a file
  --from-figma TEXT   Figma URL with optional '::annotation' (repeatable)
  --rediscover        Re-run track discovery even if a config already exists
```

Dispatch on second-and-later invocations:

| Pipeline state | What `work` does |
|---|---|
| no `.agentic-dev/` | first-run onboarding (discovery + analysis + scaffold + pipeline) |
| `COMPLETE` | enqueues the prompt as an update; restarts at `FEATURE_ANALYSIS` |
| `FAILED` | injects the prompt as feedback and auto-resumes from the failed phase |
| anything mid-pipeline | exits 1 — use `agentic-dev resume` first |

### `resume`

Continue a paused or failed pipeline.

```
Options:
  --feedback TEXT     Feedback to inject into the next agent's context
  --skip-sprint N     Mark sprint N complete and continue
```

### `remediate`

Fix failing UAT acceptance criteria by re-entering the pipeline with the
last UAT report as a remediation prompt. Increments `remediation_cycle`
in state. Intended to be run as many times as needed until UAT passes —
this is the outer ralph loop.

### `tracks`

Show the inferred tracks and their kinds.

```
Options:
  --rediscover        Re-run the discovery agent and overwrite the saved tracks
```

### `status`

Show pipeline phase, sprint progress, and total cost.

### `config`

Configure checkpoint behaviour and autonomy level.

```
Options:
  --autonomy TEXT     Autonomy level (e.g. "full")
  --checkpoints TEXT  Comma-separated list of checkpoint names
                      (after_design, after_each_sprint, before_uat)
```

### `logs`

View per-run pipeline logs or per-agent dumps from `.agentic-dev/logs/`.

```
Options:
  --run TEXT          Specific run id (defaults to latest)
  --agent TEXT        Filter to one agent's dumps
  --jsonl             Emit the raw JSONL events
```

### `cost`

Show cost breakdown by agent, phase, and sprint.

## Tech stack

- Python 3.12+
- Typer (CLI), Pydantic (models), Jinja2 (templates), Rich (terminal UI),
  PyYAML (config), httpx
- Pytest with pytest-asyncio for async tests

## Documentation

- [User guide](docs/user-guide.md) — end-to-end walkthrough of the `work`
  command, track inference, checkpoints, and artifact locations.
- [Architecture](docs/architecture.md) — module map for contributors.
- [Agents](docs/agents.md) — agent catalogue, model assignments, and the
  YAML schema.
- [Documents](docs/documents.md) — document taxonomy and producer/consumer
  flow.

## Running tests

```bash
pytest
```

## License

See [LICENSE](LICENSE).
