# Agents

## Agent Definitions

All agents are defined as YAML files in `src/agentic_dev/agents/definitions/`.

### Model Assignments

| Model | Agents | Rationale |
|---|---|---|
| **opus** | Feature Analyst, Architect, all QA agents (incl. Input Processor QA, Input Updater QA, UAT QA, Drift Detector QA), all per-kind UAT agents (`uat_web`, `uat_cli`, `uat_desktop_electron`, `uat_desktop_tauri`, `uat_mobile`, `uat_api`), Spec Reverse Engineer, Feature Extractor, Drift Detector, Design Diff, Spec Diff | Deep reasoning, critical review, multi-document synthesis |
| **sonnet** | Input Processor, Input Updater, Sprint Planner, Frontend/Backend Dev, Integration, Structure Detector, Code Analyzer, Spec Updater, Code Analyzer QA, Spec Updater QA, Spec Reverse Engineer QA, Feature Extractor QA | Structured transformation, code generation, validation, cost-effective |

### Design & Architecture Team

| Agent | QA Counterpart | Input | Output |
|---|---|---|---|
| Input Processor | Input Processor QA | User input | Structured Input |
| Input Updater | Input Updater QA | Previous Structured Input + change request | Updated Structured Input |
| Design Diff | *(none)* | Old Design Analyses, New Design Analyses | Design Changes (change summary) |
| Spec Diff | *(none)* | Old Structured Input, New Structured Input | Spec Changes (change summary) |
| Feature Analyst | Feature Analyst QA | Structured Input | Features Request |
| Architect | Architect QA | Features Request, Structured Input | Frontend Spec, Backend Spec, API Contract |
| Sprint Planner | Sprint Planner QA | Features Request, all specs | Sprint Plan |

### Frontend Team

| Agent | QA Counterpart | Input | Output |
|---|---|---|---|
| Frontend Developer | Frontend QA | Frontend Spec, API Contract, Sprint scope, `frontend_kind`, Figma Sources (when available) | Code in frontend/ repo |

Frontend Developer and Frontend QA templates branch on `frontend_kind`:

- `web` — today's guidance: pages, components, error boundaries, form validation
- `cli` — commands, stdout/stderr contract, non-interactive mode, exit codes
- `desktop` — windows, menus, IPC boundaries, packaging
- `mobile` — screens, navigation, platform lifecycle, main-thread UI rules

Tool allowlists stay identical across kinds — the split is prompt-only. See `docs/superpowers/specs/2026-04-17-multi-frontend-runtime-uat-design.md` for rationale.

When Figma sources are present, the Frontend Developer and Frontend QA agents receive the Figma URLs and a `figma_mcp_available` flag. If the Figma MCP server is configured in your Claude Code environment, agents use it directly for pixel-accurate implementation. Otherwise, they fall back to the text-based Design Analyses.

### Backend Team

| Agent | QA Counterpart | Input | Output |
|---|---|---|---|
| Backend Developer | Backend QA | Backend Spec, API Contract, Sprint scope | Code in backend/ repo |

### Integration Team

| Agent | QA Counterpart | Input | Output |
|---|---|---|---|
| Integration Agent | Integration QA | API Contract, Sprint Plan | Integration code + guide |

### Adoption Team

| Agent | QA Counterpart | Input | Output |
|---|---|---|---|
| Structure Detector | *(none)* | Project directory | Directory mapping (JSON) |
| Spec Reverse Engineer | Spec Reverse Engineer QA | Existing code + target spec type | Frontend Spec, Backend Spec, or API Contract |
| Feature Extractor | Feature Extractor QA | All generated specs | Features Request with `[EXISTING-F...]` IDs |

### Sync Team

| Agent | QA Counterpart | Input | Output |
|---|---|---|---|
| Code Analyzer | Code Analyzer QA | Code directory + scope | Code reality snapshot |
| Drift Detector | Drift Detector QA | Code snapshots + spec documents | Sync Report with `[DRIFT-nnn]` IDs |
| Spec Updater | Spec Updater QA | Current spec + resolved drift items | Updated spec document |

### QA Team

The legacy single `uat` agent is replaced by a family of per-kind UAT agents dispatched by `(ProjectType, FrontendKind)`. Each runtime-drives the product through a platform-appropriate surface and emits structured per-AC evidence.

| Agent | Driver | Tool additions beyond base | QA Counterpart |
|---|---|---|---|
| `uat_web` | Playwright MCP | `mcp__plugin_playwright_playwright__*` | `uat_qa` |
| `uat_cli` | subprocess via `Bash` | — | `uat_qa` |
| `uat_desktop_electron` | Playwright attached via CDP | `mcp__plugin_playwright_playwright__*` | `uat_qa` |
| `uat_desktop_tauri` | `tauri-driver` (WebDriver) | — | `uat_qa` |
| `uat_mobile` | Maestro (primary) or the project's integration test runner | — | `uat_qa` |
| `uat_api` | `curl` / `httpx` via `Bash` (used only when `ProjectType == backend_only`) | — | `uat_qa` |

Base tool allowlist for every UAT agent: `Read, Glob, Grep, Bash, WebFetch`. Budgets: `max_budget_usd: 5.00`, `max_turns: 50`.

**Input documents** for every per-kind UAT agent include `uat_prereqs` — a document written by the engine before dispatch that records the result of runtime probes (e.g. `maestro doctor`, `flutter devices`, Playwright MCP availability, `tauri-driver --version`). The agent reads prereq results and degrades gracefully if its driver is unavailable.

**False-PASS enforcement.** After the UAT agent completes, `uat/validator.py` structurally parses the report and forces `Overall: FAIL` if any of these hold under `uat_mode: full`:

- No AC has `Verification mode: runtime`
- Any runtime PASS AC has an empty `Artifacts:` list
- Every AC reports `Driver: none` with overall PASS
- Any PASS AC lacks concrete `Evidence:` bullets

When triggered, the validator prepends a `## Validator Override` section to the report explaining which rule failed. This is enforced in code, not just prompt.

**Artifacts.** The engine creates `.agentic-dev/uat_artifacts/<run_id>/` before dispatch. UAT agents write screenshots, subprocess transcripts, Maestro logs, WebDriver sessions, and HTTP traces there. Artifacts are referenced by path from the UAT report.

See `src/agentic_dev/uat/dispatcher.py` for the full `(ProjectType, FrontendKind)` dispatch matrix.

## Agent YAML Schema

```yaml
name: string
description: string
team: string
claude:
  model: opus|sonnet
  permission_mode: plan|bypassPermissions
  allowed_tools: [list]
  max_budget_usd: float
  use_bare_mode: true
  max_turns: 50
prompt_template: string
input_documents: [list]
output_documents: [list]
qa_agent: string|null
working_directory: string
constraints: [list]
```

## Prompt Templates

All templates are Jinja2 files in `src/agentic_dev/prompts/templates/`. Each template receives:

- `input_documents` — dict of document name → content
- `constraints` — list of constraint strings from the YAML definition
- `correction_mode` — bool, True when re-running after QA feedback
- `previous_output` — the agent's previous output (correction mode only)
- `qa_feedback` — QA agent's feedback (correction mode only)

Partials in `_partials/` provide reusable blocks for API contract context, sprint scope, and correction instructions.

In update mode, templates may also receive:
- `change_request` — text of the user's change description (targeted updates)
- `spec_changes` — summary of what changed between old and new full specs (`--full-spec` updates)
- `design_changes` — summary of what changed between old and new Figma designs (`--from-figma` updates)
- `figma_sources` — Figma URLs for direct MCP access during frontend implementation
- `figma_mcp_available` — `"true"` or `"false"` indicating whether the Figma MCP server is configured
