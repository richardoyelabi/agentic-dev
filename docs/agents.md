# Agents

## Agent Definitions

All agents are defined as YAML files in
`src/agentic_dev/agents/definitions/`. The current roster is:

```
architect.yml              feature_analyst.yml      sprint_planner.yml
architect_qa.yml           feature_analyst_qa.yml   sprint_planner_qa.yml
design_diff.yml            input_processor.yml      qa.yml
developer.yml              input_processor_qa.yml   spec_diff.yml
integration.yml            input_updater.yml        uat_api.yml
integration_qa.yml         input_updater_qa.yml     uat_cli.yml
uat_desktop_electron.yml   uat_desktop_tauri.yml    uat_mobile.yml
uat_qa.yml                 uat_web.yml
```

There is **no** separate `frontend_developer` / `backend_developer` —
sprint execution uses the single `developer` agent driven once per
in-scope track per sprint, with the track's `kind` passed in for
kind-specific prompt guidance. The QA counterpart is the single generic
`qa` agent. The legacy adoption and sync teams
(`structure_detector`, `spec_reverse_engineer`, `feature_extractor`,
`code_analyzer`, `drift_detector`, `spec_updater` and their QAs) were
removed alongside the `adopt` and `sync` commands.

### Model assignments

| Model | Agents | Rationale |
|---|---|---|
| **opus** | `architect`, `architect_qa`, `developer`, `qa`, `feature_analyst`, `input_processor`, `input_updater`, `input_updater_qa`, `spec_diff`, `sprint_planner`, `integration`, `integration_qa`, `uat_qa`, all per-kind UAT agents (`uat_web`, `uat_api`, `uat_cli`, `uat_mobile`, `uat_desktop_electron`, `uat_desktop_tauri`) | Deep reasoning, multi-document synthesis, critical review, runtime UAT |
| **sonnet** | `input_processor_qa`, `feature_analyst_qa`, `sprint_planner_qa`, `design_diff` | Structured transformation and cheap-to-run review |

The exact model IDs live in `MODELS` in
[src/agentic_dev/config.py](../src/agentic_dev/config.py).

### Design & architecture team

| Agent | QA counterpart | Input | Output |
|---|---|---|---|
| `input_processor` | `input_processor_qa` | User input | Structured Input |
| `input_updater` | `input_updater_qa` | Previous Structured Input + change request | Updated Structured Input |
| `feature_analyst` | `feature_analyst_qa` | Structured Input | Features Request |
| `architect` | `architect_qa` | Features Request, Structured Input, existing-code analyses | Per-track specs (`<track>_spec.md`) + `api_contract.md` (when any track has `kind=api`). The agent emits a single multi-document output; `orchestrator/engine.py` splits it into the per-track files. |
| `sprint_planner` | `sprint_planner_qa` | Features Request + all specs | Sprint Plan |
| `spec_diff` | *(none)* | Old Structured Input, New Structured Input | Spec Changes summary |
| `design_diff` | *(none)* | Old Design Analyses, New Design Analyses, Figma Sources | Design Changes summary |

### Track development

| Agent | QA counterpart | Input | Output |
|---|---|---|---|
| `developer` | `qa` | Track spec, API contract (when relevant), sprint scope, `track.kind`, Figma Sources + `figma_mcp_available` (when present) | Code in the track directory |

`developer` is invoked **once per in-scope track per sprint**. The
prompt template branches on the track's `kind` for kind-appropriate
guidance:

- `web` — pages, components, error boundaries, form validation
- `api` — endpoints, request/response shapes, persistence, auth
- `cli` — commands, stdout/stderr contract, non-interactive mode, exit codes
- `desktop` — windows, menus, IPC boundaries, packaging
- `mobile` — screens, navigation, platform lifecycle, main-thread UI rules
- `worker` / `library` / `generic` — fall back to generic guidance

Tool allowlists are identical across kinds — the split is prompt-only.
When Figma sources are present, the `developer` agent also receives the
Figma URLs and a `figma_mcp_available` flag. If the Figma MCP server is
configured in the project's Claude Code environment, the agent uses it
directly for pixel-accurate implementation; otherwise it falls back to
the text-based Design Analyses.

For `web`/`desktop`/`mobile` tracks the developer prompt also carries a
**Frontend Design Quality** section: it must use the `frontend-design`
skill (the `Skill` tool is in the allowlist), reproduce design tokens
exactly, build every component state, stay responsive, and avoid generic
templated AI aesthetics. Getting the design *to* the agent is necessary
but not sufficient — this section is the explicit quality bar.

### Integration

| Agent | QA counterpart | Input | Output |
|---|---|---|---|
| `integration` | `integration_qa` | API Contract, Sprint Plan | Integration code + `integration_guide.md` |

Run per sprint when the sprint plan calls for an integration step.

### UAT (per-track runtime verification)

The earlier single `uat` agent was replaced by a family of per-kind UAT
agents dispatched on `track.uat_kind` by
[uat/dispatcher.py](../src/agentic_dev/uat/dispatcher.py):

| Agent | Driver | Tool additions beyond base | QA counterpart |
|---|---|---|---|
| `uat_web` | Playwright MCP | `Monitor`, `mcp__plugin_playwright_playwright__*` | `uat_qa` |
| `uat_cli` | subprocess via `Bash` | `Monitor` | `uat_qa` |
| `uat_desktop_electron` | Playwright attached via CDP | `Monitor`, `mcp__plugin_playwright_playwright__*` | `uat_qa` |
| `uat_desktop_tauri` | `tauri-driver` (WebDriver) | `Monitor` | `uat_qa` |
| `uat_mobile` | Maestro (primary) or the project's own integration test runner | `Monitor` | `uat_qa` |
| `uat_api` | `curl` / `httpx` via `Bash` | `Monitor` | `uat_qa` |

Base tool allowlist for every UAT agent: `Read, Glob, Grep, Bash,
WebFetch`. Budgets: `max_budget_usd: 5.00`, `max_turns: 100`.

**Design fidelity (UI UAT).** `uat_web`, `uat_desktop_electron`,
`uat_desktop_tauri`, and `uat_mobile` opt into Figma MCP (`figma_mcp: true`)
and declare `figma_sources` / `figma_annotations` as inputs. When Figma
sources exist *and* the MCP server is configured (`figma_mcp_available ==
"true"`, computed once per run by `figma_mcp_available_flag`), the engine
expands the Figma tool patterns for these agents and their prompt gains a
**Design Fidelity** section: for each screen it screenshots the running UI,
pulls the matching Figma frame via `get_screenshot`, and records a per-screen
PASS/FAIL verdict on layout, spacing, colour, typography, and component
appearance. Material visual deviations count as defects in the report, so the
`remediate` loop drives them to resolution like any failed acceptance
criterion. `uat_api` and `uat_cli` have no visual surface and stay Figma-free.

**Backgrounded drivers.** UAT agents start long-running drivers
(dev servers, browsers, simulators) in the background with `Bash`'s
`run_in_background` and poll them through the `Monitor` tool, so a
single UAT cycle can drive a full real-product run without blocking.

**Prereq probes.** Before dispatch, `uat/prereqs.py` runs runtime checks
for each driver (e.g. `maestro --version` plus `maestro doctor`,
`flutter --version` plus a booted non-web device, `tauri-driver
--version`, Playwright MCP availability) and writes a structured
`uat_prereqs_<track>.md` artifact that the per-track UAT agent reads
before starting and that it uses to degrade gracefully when a driver is
unavailable.

**False-PASS enforcement.** After the action agent completes,
`uat/validator.py` parses the report and forces `Overall: FAIL` if any
of these rules trips (in `uat_mode: full`):

- no AC has `Verification mode: runtime`
- any runtime PASS AC has an empty `Artifacts:` list
- every AC reports `Driver: none` with overall PASS
- any PASS AC lacks concrete `Evidence:` bullets

When triggered, the validator prepends a `## Validator Override` section
to the report explaining which rule failed. This is enforced in code,
not just prompt.

**Artifacts.** The engine creates `.agentic-dev/uat/<run_id>/evidence/<track>/`
before dispatch. UAT agents write screenshots, subprocess transcripts,
Maestro logs, WebDriver sessions, and HTTP traces there. Artifacts are
referenced by path from the UAT report.

**Aggregation.** [uat/aggregator.py](../src/agentic_dev/uat/aggregator.py)
combines every `uat_report_<track>.md` into a single `uat_report.md`
with one `## Overall Result: PASS|FAIL` line — PASS iff every track
passed.

## Agent YAML schema

```yaml
name: string
description: string
team: string
claude:
  model: opus|sonnet
  permission_mode: bypassPermissions   # most agents
  allowed_tools: [list]                # additive on top of the base
  max_budget_usd: float
  max_turns: int                       # defaults to DEFAULT_MAX_TURNS in config.py
  timeout_s: int | null                # optional; wall-clock backstop (last resort)
                                       # for a wedged CLI — normal completion is the
                                       # CLI's own exit. Defaults to
                                       # DEFAULT_AGENT_BACKSTOP_S in config.py
  idle_timeout_s: int | null           # optional; max time with no session-transcript
                                       # progress before the CLI is treated as wedged.
                                       # Defaults to DEFAULT_AGENT_IDLE_TIMEOUT_S
  use_bare_mode: true                  # defaults to true on AgentDefinition
prompt_template: string                # filename under prompts/templates/
input_documents: [list]                # document names; can be empty
output_documents: [list]               # document names; can be empty
qa_agent: string | null
working_directory: string              # optional; defaults to "."
constraints: [list]
```

See [agents/base.py](../src/agentic_dev/agents/base.py) for the
authoritative Pydantic model.

## Prompt templates

All templates are Jinja2 files in `src/agentic_dev/prompts/templates/`.
Each template receives:

- `input_documents` — dict of document name → content
- `constraints` — list of constraint strings from the YAML definition
- `correction_mode` — bool, true when re-running after QA feedback
- `previous_output` — the agent's previous output (correction mode only)
- `qa_feedback` — the QA agent's feedback (correction mode only)

Partials in `_partials/` provide reusable blocks for API-contract
context, sprint scope, and correction instructions.

In update / remediate modes, templates may also receive:

- `change_request` — text of the user's change description (targeted
  updates) or the composed UAT-report-as-change-request (remediation)
- `spec_changes` — summary from `spec_diff`
- `design_changes` — summary from `design_diff`
- `figma_sources` — Figma URLs for direct MCP access during
  frontend implementation
- `figma_mcp_available` — `"true"` / `"false"` indicating whether the
  Figma MCP server is configured in this Claude Code environment
