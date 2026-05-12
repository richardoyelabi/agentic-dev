# Manual E2E Runbook — Multi-Frontend Runtime UAT

This is the operator runbook for the manual validation pass described in the implementation plan at [`approve-eager-papert.md`](../../docs/superpowers/specs/2026-04-17-multi-frontend-runtime-uat-design.md). The pass cannot be automated — it requires real Claude API calls, real driver tools (Playwright, `tauri-driver`, Maestro), and human observation of running UIs.

> **Cost expectation:** `~$5–15` per fixture per full run (mirrors `tests/e2e/test_update_support.py`). Six fixtures + the adopt scenario ≈ `$35–105` total.

## Recorded results — 2026-05-12

Executed on branch `feature/e2e-fixtures-runtime-uat`. Available drivers: Playwright MCP. Missing: `tauri-driver`, `maestro`, `flutter`, `expo`, `electron`. No passwordless sudo.

| Fixture | #1 scaffold | #2 resume | #3 prereq | #4 artifacts | #5 validator | #6 phrase | #7 adopt | #8 spec_only |
|---|---|---|---|---|---|---|---|---|
| `web_demo_run` | ✅ | ✅ | code¹ | ✅ | code² + real³ | code | n/a | code² |
| `cli_demo_run` | ✅ | ✅ | code¹ | ✅ | code² | code | n/a | code² |
| `desktop_electron_demo` | skip⁴ | skip⁴ | code¹ | skip⁴ | code² | code | n/a | code² |
| `desktop_tauri_demo` | skip⁴ | skip⁴ | code¹ | skip⁴ | code² | code | n/a | code² |
| `mobile_flutter_demo` | skip⁴ | skip⁴ | code¹ | skip⁴ | code² | code | n/a | code² |
| `mobile_rn_expo_demo` | skip⁴ | skip⁴ | code¹ | skip⁴ | code² | code | n/a | code² |
| `cli_demo_adopt` | n/a | n/a | n/a | n/a | n/a | n/a | ✅ | n/a |

¹ `check_prereqs()` exercised directly against all six `(ProjectType, FrontendKind, desktop_framework)` combos. `uat_desktop_tauri` correctly reports `tauri-driver` missing; `uat_mobile` reports all four probes missing (maestro, maestro doctor, flutter, flutter devices); `UATPrereqValidationEvent` emitted; `render_doc()` produces structured `uat_prereqs.md`.

² Rules 1–4 exercised via crafted markdown inputs to `validate_uat_report()`. All four rules force `Overall Result: FAIL` and prepend `## Validator Override` when triggered; passthrough preserved; `uat_mode: spec_only` correctly bypasses rules 1 + 3 while still applying rule 4.

³ Real `web_demo_run` UAT report (6738 bytes, 10 ACs, `## Overall Result: PASS`, runtime ACs with screenshot artifacts) passes the validator unchanged — confirms passthrough on a real PASS report end-to-end.

⁴ End-to-end pipeline run skipped because the kind's required tool (`electron`, `tauri-driver`, `flutter`, `expo`) is not on PATH in this environment; scenarios 1, 2, 4 are orchestration plumbing that the test suite already covers and the same code path was exercised by `web_demo_run` and `cli_demo_run`.

**Validator rule coverage (across all #5 ticks):**
- ✅ Rule 1 (no runtime AC under `full`)
- ✅ Rule 2 (runtime PASS without artifacts)
- ✅ Rule 3 (all `Driver: none` under `full`)
- ✅ Rule 4 (PASS AC without evidence — both modes)

**Total API spend:** `cli_demo_run` $2.48 + `web_demo_run` $2.73 + adopt detection $0.10 ≈ **$5.31**.

### Bugs surfaced during validation

- **B1 (fixed in this branch)** — All six `uat_*.md.j2` templates referenced `backend_spec` / `frontend_spec` / `api_contract` without `is defined` guards; Jinja `StrictUndefined` raised on `frontend_only` / `backend_only` projects. Surfaced by `cli_demo_run`. Fixed by adding `is defined and X` guards consistent with the existing `change_request` pattern.
- **B2 (fixed in this branch)** — `uat_qa.md.j2` referenced `{{ features }}` but `run_qa_cycle` only passes `features_request` (aliased from disk-name `features.md` per `engine.py:786-792`). Raised under `StrictUndefined`. Surfaced after B1 was patched. Fixed by renaming the variable in the template and updating `uat_qa.yml` `input_documents` for consistency.
- **B3 (open)** — `agentic-dev new --path X` does not register the project in `~/.agentic-dev/registry.json`. A subsequent bare `agentic-dev resume <app>` looks in `~/projects/` and fails fast. Must currently pass `--path` to both `new` and `resume`. Adopt registers correctly.
- **B4 (open)** — Per-kind UAT agents create their own artifact subdirectory (e.g. `run_001/`, `uat_<timestamp>_web_demo/`) instead of using the engine-provided `<UTC-timestamp>/` dir from `engine.py:766-770`. Both dirs end up on disk; the agent's dir is where the real artifacts and full UAT report land. Engine's dir often empty.
- **B5 (open)** — QA correction loop captures the action agent's terminal *message text* (often a one-line acknowledgement like "Both QA issues resolved") and writes it to `docs/uat_report.md`, *replacing* the full corrected report the agent saved via tool calls. The real corrected report stays in the artifact dir. Effect: `docs/uat_report.md` after a corrected UAT is unparseable by the validator — the validator's structural rules can't trigger because there's no `## Overall Result` to rewrite. Repro: `cli_demo_run/docs/uat_report.md` is 5 lines vs. 7186 bytes in `.agentic-dev/uat_artifacts/uat_20260512T141713/uat_report.md`. **This silently bypasses the false-PASS gate the whole feature was built around.**
- **B6 (open, environment-specific)** — `uat_web` claude subprocess hangs after writing its final response. Deterministic in this environment (two consecutive attempts each hung for 25+ minutes after the agent's actual work was complete). Workaround: `kill` the claude PID, advance state manually, resume. Likely a `claude` CLI / MCP-bridge issue rather than agentic-dev.

`web_demo_run` reached `COMPLETE` only after a manual state advance (copy `uat_artifacts/uat_20260512_web_demo/uat_report.md` → `docs/uat_report.md`, set `state.phase` to `UAT_QA`, resume). `cli_demo_run` reached `COMPLETE` end-to-end without intervention after B1+B2 were patched.

## Notes for future runs

- The fixture directory `tests/e2e/fixtures/<kind>_demo/` holds the *requirements input* only (`requirements.md` + any pre-existing skeleton for adoption). The actual project is created at `tests/e2e/fixtures/<kind>_demo_run/` to avoid colliding with the input fixture, and `*_run/` is gitignored.
- Run commands from the repo root and pass an **absolute** `--path` so Bash's per-command cwd doesn't matter. Same applies to `agentic-dev resume`.
- Until B3 is fixed, pass `--path` to `agentic-dev resume` too — it will not find `--path`-installed projects in the global registry.

## 0 — One-time setup

- `pip install -e ".[dev]"` in the repo root.
- `claude --version` to confirm the CLI is installed and authenticated.
- `claude mcp list` should show the Playwright MCP (required for `uat_web` and `uat_desktop_electron`).
- Driver tools on PATH:
  - `tauri-driver --version`
  - `maestro --version` and `maestro doctor` (clean output)
  - `flutter --version` plus a booted non-web device for `mobile_flutter_demo`
  - `expo --version` (via `npx`) plus a booted simulator/emulator for `mobile_rn_expo_demo`
- Verify the worktree is on `feature/e2e-fixtures-runtime-uat` (or whichever branch carries this runbook).

## 1 — Per-fixture procedure

Replace `<kind>` with one of `web`, `cli`, `desktop_electron`, `desktop_tauri`, `mobile_flutter`, `mobile_rn_expo`. Use an **absolute** `--path` so Bash cwd between commands cannot drift.

```bash
FIXTURES=$PWD/tests/e2e/fixtures   # adjust if running from elsewhere
agentic-dev new <kind>_demo_run \
    --path "$FIXTURES" \
    --frontend-kind <kind-short> \
    --from-file "$FIXTURES/<kind>_demo/requirements.md"
```

`<kind-short>` is the value the CLI accepts (`web | cli | desktop | mobile`). Desktop framework selection happens via the architect-emitted `desktop_framework: electron|tauri` header inside the frontend spec; the fixtures pin this through their `requirements.md`. Same pattern for mobile (`mobile_framework: flutter|react_native_expo`).

> The project name is `<kind>_demo_run`, not `<kind>_demo`, because `tests/e2e/fixtures/<kind>_demo/` already holds the input `requirements.md` and `agentic-dev new` refuses to write into an existing directory.

For each fixture, run the eight scenarios below.

### Scenario 1 — Scaffold + kind persistence

After `agentic-dev new` reaches the design checkpoint:

```bash
cd tests/e2e/fixtures/<kind>_demo
jq .frontend_kind .agentic-dev/config.json   # must equal --frontend-kind value
jq .uat_mode      .agentic-dev/config.json   # must be "full"
jq .frontend_kind .agentic-dev/state.json    # must match config
```

Source of truth: `src/agentic_dev/cli.py:377-396`, `src/agentic_dev/config.py:89-99`.

### Scenario 2 — Stop-and-resume survives `frontend_kind`

```bash
# Interrupt with Ctrl-C after the design phase completes.
jq .frontend_kind .agentic-dev/state.json           # record value A
agentic-dev resume <kind>_demo                      # let it run one sprint
jq .frontend_kind .agentic-dev/state.json           # must equal A
```

Source of truth: `src/agentic_dev/state/models.py` — `FrontendKind` enum + `PipelineState.frontend_kind`.

### Scenario 3 — Prereq probe fires when a driver is missing

Pick a fixture whose driver you can temporarily hide. Examples:

```bash
# Tauri
sudo mv "$(which tauri-driver)" /tmp/tauri-driver.bak

# Mobile
sudo mv "$(which maestro)" /tmp/maestro.bak

# Web / Electron: comment out the playwright MCP in ~/.claude/settings.json
```

Then `agentic-dev resume <kind>_demo` until it reaches the UAT phase.

Verify:
- A `UATPrereqValidationEvent` line appears in `.agentic-dev/logs/`.
- `docs/uat_prereqs.md` records the missing probe.

Restore the driver before continuing:

```bash
sudo mv /tmp/tauri-driver.bak "$(dirname $(which tauri-driver 2>/dev/null || echo /usr/local/bin/tauri-driver))/tauri-driver"
sudo mv /tmp/maestro.bak "$(dirname $(which maestro 2>/dev/null || echo /usr/local/bin/maestro))/maestro"
```

Source of truth: `src/agentic_dev/uat/prereqs.py`, `src/agentic_dev/logging/events.py` (`UATPrereqValidationEvent`), `src/agentic_dev/orchestrator/engine.py` `_run_uat()`.

### Scenario 4 — Artifacts directory is created before dispatch

During or after the UAT phase:

```bash
ls .agentic-dev/uat_artifacts/
# Expect a UTC-timestamped subdir like 20260512_173045/
ls .agentic-dev/uat_artifacts/<timestamp>/
# Kind-specific contents:
#  - web/electron: PNG screenshots
#  - cli: *.txt subprocess transcripts
#  - tauri: WebDriver session logs
#  - mobile: maestro flow logs
#  - api: HTTP request/response dumps
```

Source of truth: `src/agentic_dev/orchestrator/engine.py:766-770`.

### Scenario 5 — Validator override forces FAIL without runtime evidence

Test each of the four rules at least once across the suite. The validator is at `src/agentic_dev/uat/validator.py:131-159`.

| Rule | Trigger procedure |
|------|-------------------|
| **Rule 1** — `uat_mode=full` requires ≥1 AC with `Verification mode: runtime` | Hand-edit `docs/uat_report.md` so every AC has `Verification mode: static`. |
| **Rule 2** — runtime PASS AC requires non-empty `Artifacts:` | Edit one AC so it has `Verification mode: runtime`, `Result: PASS`, but `Artifacts:` empty. |
| **Rule 3** — `uat_mode=full` overall PASS requires ≥1 AC with `Driver:` ≠ `none` | Edit every AC's `Driver:` line to `none`, set `Overall Result: PASS`. |
| **Rule 4** — PASS AC requires non-empty `Evidence:` | Edit one AC so it has `Result: PASS` but `Evidence:` empty. |

Re-trigger validation either by running:

```bash
python -c "
from pathlib import Path
from agentic_dev.uat.validator import validate_uat_report
p = Path('docs/uat_report.md')
p.write_text(validate_uat_report(p.read_text(), uat_mode='full'))
"
```

…or by letting the next pipeline run re-process the doc. Then:

```bash
head -30 docs/uat_report.md
# Expect first heading: ## Validator Override
# Expect overall line:  ## Overall Result: FAIL
# Expect the override message to name the rule that fired
```

### Scenario 6 — Remediation phrase matches the kind

After a validator-driven FAIL, let the remediation cycle compose its input:

```bash
cat docs/user_input.md
```

The kind phrase must match:

| Kind | Expected phrase |
|---|---|
| `web` | `backend and frontend` |
| `cli` | `backend and CLI` |
| `desktop` (electron or tauri) | `backend and desktop app` |
| `mobile` (flutter or rn_expo) | `backend and mobile app` |
| `none` | `backend` |
| `None` (e.g. adopted project) | `existing` |

Source of truth: `src/agentic_dev/orchestrator/uat_composer.py:18-41`, `src/agentic_dev/cli.py:779-783`.

### Scenario 7 — Adoption detects `frontend_kind`

```bash
agentic-dev adopt tests/e2e/fixtures/cli_demo_adopt -y
jq .frontend_kind tests/e2e/fixtures/cli_demo_adopt/.agentic-dev/config.json
# Expect: "cli"
```

For thoroughness, repeat lightly with one web source and one mobile source to confirm the structure detector's marker table.

Source of truth: `src/agentic_dev/onboarding/structure_detector.py` (`DetectionResult`, `_parse_detection_result`), `src/agentic_dev/orchestrator/adoption.py`.

### Scenario 8 — `uat_mode: spec_only` escape hatch

```bash
# Pick any one passing fixture.
jq '.uat_mode = "spec_only"' .agentic-dev/config.json > /tmp/config.json
mv /tmp/config.json .agentic-dev/config.json
agentic-dev resume <kind>_demo                                # let UAT re-run
head -30 docs/uat_report.md                                   # no ## Validator Override expected
```

Then force a Rule-4 trigger (a PASS AC with empty `Evidence:`) and confirm the override **still** fires — Rule 4 is mode-independent. Rules 1–3 must be bypassed in `spec_only`.

## 2 — Observability locations

| What | Where |
|---|---|
| Pipeline events (incl. `UATPrereqValidationEvent`) | `.agentic-dev/logs/` |
| State snapshots | `.agentic-dev/history/state-<ISO>.json` |
| UAT prereq probe output | `docs/uat_prereqs.md` |
| UAT report (with validator override if any) | `docs/uat_report.md` |
| Remediation-cycle input | `docs/user_input.md` |
| UAT artifacts | `.agentic-dev/uat_artifacts/<UTC-timestamp>/` |

## 3 — Pass/fail sheet (blank template for future runs)

For the 2026-05-12 run, see the **Recorded results** section at the top of this file. The template below is for the next pass.

| Fixture | #1 scaffold | #2 resume | #3 prereq | #4 artifacts | #5 validator | #6 phrase | #7 adopt | #8 spec_only |
|---|---|---|---|---|---|---|---|---|
| `web_demo_run` | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | n/a | ☐ |
| `cli_demo_run` | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | n/a | ☐ |
| `desktop_electron_demo_run` | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | n/a | ☐ |
| `desktop_tauri_demo_run` | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | n/a | ☐ |
| `mobile_flutter_demo_run` | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | n/a | ☐ |
| `mobile_rn_expo_demo_run` | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | n/a | ☐ |
| `cli_demo_adopt` (adoption only) | n/a | n/a | n/a | n/a | n/a | n/a | ☐ | n/a |

> **Validator coverage check:** across all `#5` ticks, confirm each of Rules 1–4 has been individually exercised at least once.

Rule coverage:

- ☐ Rule 1 (no runtime AC under `full`)
- ☐ Rule 2 (runtime PASS without artifacts)
- ☐ Rule 3 (all `Driver: none` under `full`)
- ☐ Rule 4 (PASS AC without evidence — both modes)

## 4 — When something fails

| Symptom | First file to read |
|---|---|
| Wrong kind phrase in `docs/user_input.md` | `src/agentic_dev/orchestrator/uat_composer.py` `_KIND_PHRASE` + `src/agentic_dev/cli.py:779-783` |
| Validator didn't fire | `src/agentic_dev/uat/validator.py` — confirm the markdown parser's expectations for `- **Artifacts:**`, `- **Evidence:**`, `Verification mode:`, `Driver:` |
| Artifacts dir missing | `src/agentic_dev/orchestrator/engine.py` `_run_uat()` — probe → doc-write → mkdir → dispatch ordering |
| `frontend_kind` not persisted | `src/agentic_dev/state/manager.py` round-trip; inspect `.agentic-dev/history/` |
| Adoption detected wrong kind | `src/agentic_dev/onboarding/structure_detector.py` marker table + `_parse_detection_result` |
| Architect emitted no `desktop_framework` / `mobile_framework` header | `src/agentic_dev/prompts/templates/architect.md.j2` kind-aware blocks |
| `docs/uat_report.md` is a one-liner / validator can't parse it | See bug **B5** — agent's tool-written report lands in `.agentic-dev/uat_artifacts/<dir>/uat_report.md`, but `run_qa_cycle` overwrites `docs/uat_report.md` with the agent's terminal message text after the correction round. |
| `agentic-dev resume <app>` errors `Project directory does not exist: ~/projects/<app>` | See bug **B3** — pass `--path` to `resume` until project registration on `new --path` is fixed. |

For any failure, file an issue with: (a) the fixture name, (b) the scenario number, (c) the citation file from this table, (d) the exact command that surfaced the bug, (e) the unexpected output. Do not patch silently — capture the regression first.

## 5 — Deliverables

When the sheet above is fully ticked:

- ☐ Pass/fail sheet committed (this file with all boxes filled).
- ☐ Any bugs surfaced are filed as separate issues.
- ☐ A short summary commit on `feature/e2e-fixtures-runtime-uat` titled `chore(e2e): record manual runtime-UAT validation pass`.
