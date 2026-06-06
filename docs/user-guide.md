# User Guide

agentic-dev is a deterministic pipeline you point at any project. Plan →
architecture → sprints with QA → UAT. The tool figures out the project
structure; you describe what you want done.

## Prerequisites

1. Python 3.12+
2. Claude Code CLI installed and authenticated (`claude --version` works)
3. Install agentic-dev: `pip install -e ".[dev]"`

## The primary command: `agentic-dev work`

`cd` into any project directory and tell it what you want:

```bash
cd /path/to/my/project
agentic-dev work "add a referrals feature where users earn credits"
```

On first invocation in a directory:

1. A discovery Claude agent inspects the project and infers tracks
   (sub-codebases with a coherent build/run/test loop, e.g. `backend/`
   API + `frontend/` web app + `workers/` worker).
2. Existing code in each track is analysed in parallel; results land in
   `.agentic-dev/artifacts/track_<name>_analysis.md` and feed into the
   architect so it reverse-engineers a spec from what's there rather than
   designing from scratch.
3. `.agentic-dev/` is scaffolded in the project root. Your existing files
   are untouched.
4. The deterministic pipeline runs end-to-end: plan → architecture →
   sprints with QA → UAT.

On subsequent invocations, the same command dispatches on state:

| Pipeline state | What `work` does |
|---|---|
| no `.agentic-dev/` | first-run onboarding (see above) |
| `COMPLETE` | treats the prompt as an update; restarts at `FEATURE_ANALYSIS` |
| `FAILED` | injects the prompt as feedback and auto-resumes |
| anything mid-pipeline | exits 1 — use `agentic-dev resume` first |

### Alternate input channels

```bash
# Read requirements from a file (useful for long docs with blank lines).
agentic-dev work --from-file requirements.md

# Attach Figma designs (the architect treats designer annotations as
# authoritative). Repeatable; ``::`` adds an annotation.
agentic-dev work "redesign onboarding" \
  --from-figma "https://figma.com/file/abc::onboarding wireframes"

# Re-run discovery before running the pipeline.
agentic-dev work --rediscover
```

## Track inference and overrides

agentic-dev infers tracks automatically, but if you want explicit control —
or to skip the Claude discovery call entirely — drop an `agentic-dev.yaml`
at the project root and commit it. The file is authoritative; the discovery
agent is skipped when it's present.

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

Inspect what's persisted:

```bash
agentic-dev tracks
agentic-dev tracks --rediscover   # re-run discovery and overwrite the saved tracks
```

## Other commands (all operate on the cwd-resolved project)

```bash
agentic-dev status              # pipeline phase, sprints, cost
agentic-dev resume              # continue a paused/failed pipeline
agentic-dev resume --feedback "be more conservative with deletions"
agentic-dev resume --skip-sprint 3
agentic-dev remediate           # re-enter the pipeline using the last UAT report
agentic-dev config --autonomy full
agentic-dev config --checkpoints after_design,before_uat
agentic-dev logs                # latest pipeline run log
agentic-dev logs --agent architect
agentic-dev cost                # cost breakdown by agent/sprint
```

There is no `agentic-dev new` or `agentic-dev update`. Both are subsumed
by `work` — first call onboards, subsequent calls enqueue updates.

## Following progress

In an interactive terminal, `agentic-dev work` shows a live dashboard:

- a **status** line — current phase, the active agent, sprint progress,
  cumulative cost, and elapsed time;
- a **"Now"** panel — the current agent's last few actions as they happen
  (e.g. `Read existing_code_analyses.md`, `Edit src/api/routes.py`,
  `Bash pytest`, or `writing…`), so you can follow what an agent is doing
  during the minutes it runs, without the screen filling up;
- an **Events** log — coarse milestones only (phase transitions, agent
  start/finish, QA verdicts, sprint boundaries).

The terminal stays concise on purpose. The full, fine-grained activity stream
is always written to the run's log files regardless of what the terminal shows:

- `.agentic-dev/logs/runs/<run_id>/events.jsonl` — every event as JSON
  (e.g. `grep agent_activity` to replay what an agent did);
- `.agentic-dev/logs/runs/<run_id>/pipeline.log` — the same, human-readable;
- `.agentic-dev/logs/latest` — symlink to the most recent run.

## Checkpoints

By default the pipeline pauses after design (architecture + sprint plan)
so you can review specs before code is written. Skip the pause with
`agentic-dev config --autonomy full`, or enable additional checkpoints:

```bash
agentic-dev config --checkpoints after_design,after_each_sprint,before_uat
```

Resume after a checkpoint with `agentic-dev resume`. Inject feedback with
`agentic-dev resume --feedback "use TanStack Query, not Redux"`.

## Where artifacts live

Everything the agency produces is under `<project>/.agentic-dev/artifacts/`:

- `<track>_spec.md` — per-track architecture spec
- `api_contract.md` — cross-track API contract (when any track is `kind=api`)
- `sprint_plan.md` — sprint plan with `Tracks in scope:` lines
- `track_<name>_analysis.md` — per-track existing-code analysis fed to the architect
- `existing_code_analyses.md` — concatenated existing-code analyses for the architect
- `figma_sources.md` — Figma URLs and user-supplied annotations
- `qa/<name>.md` — per-step QA reports
- `uat_prereqs_<track>.md` — per-track UAT prerequisite probe report
- `uat_report_<track>.md` and `uat_report.md` — per-track and aggregated UAT verdicts
- `.agentic-dev/uat/<run_id>/evidence/<track>/...` — UAT screenshots and transcripts

The pipeline overwrites artifacts in place; per-cycle history is in
`.agentic-dev/history/state-*.json` and the git history of the project repo.

## Notes for adopting an existing project

Just `cd` into it and run `agentic-dev work "<your prompt>"`. The discovery
pass treats it as a multi-track adoption, the analyses go to the architect,
and the architect reverse-engineers per-track specs that reflect the actual
code. Existing files are never touched by the scaffolder; the developer
agents have no `Delete` tool and modify in place.

If discovery doesn't infer your layout correctly, drop an `agentic-dev.yaml`
at the project root (see [Track inference](#track-inference-and-overrides))
and re-run.
