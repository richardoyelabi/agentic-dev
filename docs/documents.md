# Document Taxonomy

## Documents

| Document | Producer | Consumers | Filename |
|---|---|---|---|
| Structured Input | Input Processor | Feature Analyst | `structured_input.md` |
| Features Request | Feature Analyst | Architect, Sprint Planner | `features_request.md` |
| Frontend Spec | Architect | Frontend Dev, Frontend QA | `frontend_spec.md` |
| Backend Spec | Architect | Backend Dev, Backend QA | `backend_spec.md` |
| API Contract | Architect | All dev agents, Integration | `api_contract.md` |
| Sprint Plan | Sprint Planner | Sprint Runner, all devs | `sprint_plan.md` |
| Integration Guide | Integration Agent | User, UAT | `integration_guide.md` |
| QA Reports | All QA agents | Action agents | `qa_reports/<agent>_qa.md` |
| UAT Report | UAT Agent | User | `uat_report.md` |
| Design Analyses | Figma Analyzer | Architect, Spec Reverse Engineer, Design Diff | `design_analyses.md` |
| Figma Sources | CLI (`new`, `adopt`, `update`) | Frontend Dev, Frontend QA | `figma_sources.md` |
| Design Changes | Design Diff | All downstream agents, UAT | `design_changes.md` |
| Spec Changes | Spec Diff | All downstream agents, UAT | `spec_changes.md` |
| Sync Change Request | Sync resolver | Update command (`--from-sync`) | `sync_change_request.md` |

## No-Duplication Principle

- The **API Contract** is the single source of truth for the frontend/backend interface
- Frontend Spec describes UI but does not repeat endpoint details
- Backend Spec describes services but does not repeat endpoint details
- Sprint Plan references features by ID from the Features Request

## Document Flow

Text and design are **parallel channels** — both flow independently and are distributed to all downstream agents.

```
Text Input                   Figma URLs
    │                            │
    ▼                            ▼
Structured Input           Design Analyses
    │                            │
    │    (on update)             │    (on update)
    │   Spec Diff                │   Design Diff
    │       │                    │       │
    │       ▼                    │       ▼
    │  Spec Changes         Design Changes
    │       │                    │
    └───────┴────────────────────┘
                    │
                    ▼ (extra_context to all agents below)
            Features Request ──── (QA review)
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
    Frontend Spec  Backend Spec  API Contract ── (QA review)
                    │
                    ▼
               Sprint Plan ──── (QA review)
                    │
                    ▼
         ┌─── Sprint N ──────────────┐
         │ Backend code               │
         │ Frontend code (+ Figma)    │── (per-sprint QA)
         │ Integration                │
         └────────────────────────────┘
                    │
                    ▼
               UAT Report
               (receives change_request + spec_changes + design_changes)
```

## Document Schemas

See the design spec at `docs/superpowers/specs/2026-03-28-agentic-dev-agency-design.md` for detailed markdown schemas for each document type.
