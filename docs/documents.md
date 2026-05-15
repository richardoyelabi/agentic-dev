# Document Taxonomy

All agent artifacts live under `<project>/.agentic-dev/artifacts/`. There
is no top-level `docs/` directory in the host project — user-facing
documentation lives inside each track's own codebase (`README.md`,
`ARCHITECTURE.md`).

## Documents

| Document | Producer | Consumers | Filename |
|---|---|---|---|
| Structured Input | `input_processor` (`input_updater` on update) | `feature_analyst`, `input_processor_qa`, `spec_diff` | `structured_input.md` |
| Features Request | `feature_analyst` | `architect`, `sprint_planner`, all UAT agents | `features_request.md` |
| Per-track spec | engine (splits architect output) | `developer`, `qa`, UAT agents, `sprint_planner` | `<track>_spec.md` |
| API Contract | `architect` (split from multi-doc output) | `developer`, `qa`, `integration`, UAT agents | `api_contract.md` (only when any track has `kind=api`) |
| Sprint Plan | `sprint_planner` | sprint runner, `developer`, `integration`, UAT agents | `sprint_plan.md` |
| Per-track existing-code analysis | `onboarding/analyzer.py` (first-run only) | `architect` | `track_<name>_analysis.md` |
| Existing-code analyses (concatenated) | `onboarding/analyzer.py` | `architect` | `existing_code_analyses.md` |
| Integration Guide | `integration` | user, UAT, `integration_qa` | `integration_guide.md` |
| Per-agent QA reports | each `*_qa` agent | the action agent on correction | `qa/<name>.md` |
| Per-track UAT prereq report | `uat/prereqs.py` | per-track UAT agent | `uat_prereqs_<track>.md` |
| Per-track UAT report | per-kind UAT agent | `uat_qa`, `uat/aggregator.py`, user | `uat_report_<track>.md` |
| Aggregated UAT report | `uat/aggregator.py` | user, `agentic-dev remediate` | `uat_report.md` |
| Figma Sources | CLI (`work --from-figma`) | `developer`, `qa`, `design_diff` | `figma_sources.md` |
| Design Analyses | Figma analyzer (`onboarding/figma.py`) | `architect`, `design_diff` | `design_analyses.md` |
| Design Changes | `design_diff` | downstream agents on update | `design_changes.md` |
| Spec Changes | `spec_diff` | downstream agents on update | `spec_changes.md` |

The document-name → filename mapping for canonical documents lives in
[documents/models.py](../src/agentic_dev/documents/models.py); per-track
artifacts and per-sprint scope files are written directly through
[documents/store.py](../src/agentic_dev/documents/store.py) which
auto-appends `.md`.

## No-duplication principle

- The **API Contract** is the single source of truth for cross-track
  interfaces. Per-track specs reference it but never duplicate endpoint
  details.
- Per-track specs describe one codebase each and do not repeat content
  that belongs in the API Contract.
- Sprint Plan references features by ID from the Features Request, not
  by reproduction.

## Document flow

Text and design are **parallel channels** — both flow independently and
are distributed to all downstream agents via `extra_context`.

```
Text Input                         Figma URLs
    │                                  │
    ▼                                  ▼
Structured Input              Design Analyses
    │                                  │
    │  (on update)                     │  (on update)
    │  Spec Diff                       │  Design Diff
    │      │                           │      │
    │      ▼                           │      ▼
    │  Spec Changes              Design Changes
    │      │                           │
    └──────┴───────────┬───────────────┘
                       │
                       ▼  (extra_context to all agents below)

         (first-run only:
          per-track analyzer ─► existing_code_analyses)
                       │
                       ▼
              Features Request ───── (QA review)
                       │
                       ▼
                  Architect ───── (QA review)
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   <track A>_spec  <track B>_spec  api_contract
                       │            (iff kind=api)
                       ▼
                  Sprint Plan ───── (QA review)
                       │
                       ▼
        ┌─── per sprint, per in-scope track ─────┐
        │   developer + qa (one cycle per track) │
        │   integration + integration_qa         │
        │   (when scoped)                        │
        └────────────────────────────────────────┘
                       │
                       ▼
        ┌──── per track with uat_kind ───┐
        │   prereqs ─► UAT agent ─► QA   │
        │   (Validator override gate)    │
        └────────────────────────────────┘
                       │
                       ▼
                 uat_report.md
            (## Overall Result: PASS | FAIL)
                       │
                       ▼
                if FAIL: `agentic-dev remediate`
            (composes uat_report as change request,
             re-enters at INPUT_PROCESSING)
```

The legacy `sync_change_request` document — and the entire sync flow that
produced it — was removed along with the `adopt` and `sync` commands.
Drift is now handled by editing specs in `.agentic-dev/artifacts/` and
re-running the pipeline.
