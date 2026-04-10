# Adopt & Sync: Project Onboarding and Continuous Synchronization

**Date:** 2026-04-07
**Status:** Draft

## Problem

Agentic-dev can only work with projects it created from scratch. This limits it to greenfield development. Real-world usage requires:

1. **Adopting existing projects** — pointing agentic-dev at a codebase (with optional Figma designs) and having it reverse-engineer the full spec suite so the project becomes a first-class citizen that supports update, resume, remediate, and sprint commands.
2. **Continuous synchronization** — after adoption (or at any point), detecting drift between code, specs, and Figma designs, and letting the user resolve it in any direction they choose.

The current onboarding (`--from-codebase`, `--from-figma`) is a one-shot import that produces a lightweight analysis document appended to `user_input`. It does not produce real spec documents and does not support ongoing sync.

## Design Overview

Two new commands built on a unified model:

- **`agentic-dev adopt <path>`** — Entry point for existing projects. Creates `.agentic-dev/` in-place, detects directory structure, and runs spec reverse-engineering agents to produce the full spec suite. After adoption, the project is indistinguishable from one agentic-dev built from scratch.
- **`agentic-dev sync [app-name]`** — The universal drift resolver. Analyzes all sources (code, specs, Figma), produces a drift report, and lets the user decide per-item how to resolve. Works identically on adopted and native projects.

`adopt` is conceptually "first sync" — it's sync run on a project with no existing specs. After that, every invocation of `sync` works the same way regardless of project origin.

Additionally, a `--check-sync` flag on `update` and `resume` commands provides lightweight drift warnings before those commands execute.

---

## 1. Project Configuration & Directory Mapping

### 1.1 ProjectConfig Model

Stored in `.agentic-dev/config.json`, extending the existing checkpoint config:

```python
class DirectoryMap(BaseModel):
    frontend: str | None = None   # relative path from project root, e.g. "client"
    backend: str | None = None    # relative path, e.g. "server" or "."
    root: str = "."               # always project root

class ExternalSource(BaseModel):
    value: str                    # filesystem path or URL
    annotation: str = ""          # human description, e.g. "Frontend React app"

class ProjectConfig(BaseModel):
    app_name: str
    directory_map: DirectoryMap = DirectoryMap()
    sources: dict[str, list[ExternalSource]] = {}  # keys: "codebases", "figma"
    checkpoint: CheckpointConfig = CheckpointConfig()
    sync_ignores: list[str] = []  # drift item IDs marked as intentional divergence
```

### 1.2 How Directory Mapping Works

- On `adopt`, a `structure_detector` agent scans the project (package.json locations, framework markers, directory names) and proposes a mapping. The user confirms or overrides via interactive prompt.
- On `new`, the mapping defaults to `frontend/` and `backend/` as today.
- The entire pipeline reads from `directory_map` instead of hardcoded paths: `WorkspaceManager`, `SprintRunner`, `engine._setup_workspaces()`, and CLAUDE.md generation all resolve directories through the map.

### 1.3 Backwards Compatibility & Config Migration

Existing `config.json` files store a flat `CheckpointConfig` (e.g., `{"after_design": true, "after_each_sprint": false, "before_uat": false}`). The new `ProjectConfig` nests this under a `checkpoint` key. Loading old configs as `ProjectConfig` directly would lose checkpoint preferences.

**Required migration:** When loading `config.json`, detect the format:
- If the JSON contains top-level `after_design`/`after_each_sprint`/`before_uat` keys (old format), wrap them in a `checkpoint` sub-object and apply `ProjectConfig` defaults for all new fields.
- If the JSON contains a `checkpoint` key (new format), deserialize as `ProjectConfig` directly.

This migration runs on every config load (idempotent). On the next config save, the file is written in the new format.

After migration, existing projects get default values for new fields:
- `directory_map = {frontend: "frontend", backend: "backend", root: "."}`
- `sources = {}`
- `sync_ignores = []`

---

## 2. The `adopt` Command

### 2.1 CLI Interface

```bash
# Basic adoption
agentic-dev adopt /path/to/my-project

# With Figma designs
agentic-dev adopt /path/to/my-project --from-figma "https://figma.com/file/abc::Main UI"

# With new requirements (hybrid adopt + extend)
agentic-dev adopt /path/to/my-project --extend "Add a dark mode toggle and admin dashboard"

# Explicit directory mapping override
agentic-dev adopt /path/to/my-project --frontend client --backend server
```

### 2.2 Execution Steps

1. **Validate** — Check the path exists and has no `.agentic-dev/` directory already. Check the app name is not already registered in the global project registry.
2. **Create `.agentic-dev/`** — Initialize state, config, logs, and `docs/` directories in-place at the project root. If the project already has a `docs/` directory, create agentic-dev specs in `docs/agentic-dev/` instead and record this in `ProjectConfig`.
3. **Register project** — Add an entry to the global project registry (see Section 2.4).
4. **Detect directory mapping** — `structure_detector` agent scans the project structure and proposes a mapping (e.g., "frontend=client, backend=api"). The user confirms or overrides. If `--frontend`/`--backend` flags are provided, skip detection and use those.
5. **Detect project type** — `fullstack`, `frontend_only`, or `backend_only` based on which directories exist in the mapping.
6. **Run spec generation** — Purpose-built reverse-engineering agents analyze the actual code and are reviewed by QA (see Section 4.7):
   - **In parallel:** `spec_reverse_engineer` reads frontend code → `frontend_spec.md` (QA-reviewed), `spec_reverse_engineer` reads backend code → `backend_spec.md` (QA-reviewed)
   - **Then:** `spec_reverse_engineer` reads generated frontend/backend specs + code → `api_contract.md` (QA-reviewed). The agent receives the previously generated specs as input context alongside the code, so it can cross-reference both sides without re-reading all code.
   - **Then:** `feature_extractor` reads all specs → `features.md` (QA-reviewed, all features prefixed `[EXISTING-F001]`, `[EXISTING-F002]`, etc.)
   - **Finally:** Produce `structured_input.md` summarizing the project.
   - Agents are skipped based on project type (e.g., no `frontend_spec` for `backend_only`).
7. **Incorporate Figma** (if `--from-figma`) — Figma analyzer runs as today. Results saved as `design_analyses.md` and fed into the frontend spec reverse-engineering agent for design token incorporation.
8. **Incorporate new requirements** (if `--extend`) — New requirements are merged with extracted features. New features get normal `[F001]` IDs; existing features retain `[EXISTING-F...]` IDs. The pipeline continues from `INPUT_PROCESSING` through `DESIGN_CHECKPOINT` (pause for review).
9. **Save state** — If no `--extend`, state is set to `ADOPTED` with `origin="adopted"` and `last_sync_at=now`. If `--extend`, state transitions to `INPUT_PROCESSING`.
10. **Report** — Print summary: features extracted, endpoints mapped, pages documented.
11. **Error handling** — If any agent fails during adoption, the `.agentic-dev/` directory is preserved with state set to `FAILED` and `failed_at_phase=ADOPTING`. The user can resume with `agentic-dev resume --path /path/to/project` to retry from the failed step. Partial specs from completed steps are kept.

### 2.4 Project Registry

**Problem:** `WorkspaceManager.get_project_dir()` resolves projects as `DEFAULT_PROJECTS_DIR / app_name`. Adopted projects live at arbitrary paths (e.g., `/home/user/my-saas`), so `agentic-dev sync my-saas` would fail.

**Solution:** A global project registry at `~/.agentic-dev/registry.json` mapping app names to absolute paths:

```json
{
  "my-saas": "/home/user/my-saas",
  "new-app": "/home/user/projects/new-app"
}
```

- `adopt` registers the project at its actual path.
- `new` registers at `DEFAULT_PROJECTS_DIR / app_name` (preserving current behavior).
- All commands (`sync`, `update`, `resume`, `status`, etc.) resolve project paths through the registry first, falling back to `DEFAULT_PROJECTS_DIR / app_name` for backwards compatibility with projects created before the registry existed.
- `WorkspaceManager.get_project_dir()` is updated to check the registry.

### 2.5 The `--extend` Flag

When `--extend` is used, adoption transitions into the standard pipeline:

1. Specs are generated from existing code (steps 1-6 above).
2. Input Processor receives the structured_input (with existing features) plus the new requirements.
3. Feature Analyst adds new features alongside existing ones.
4. Architect updates specs to incorporate new features.
5. Sprint Planner creates sprints for **new features only** — existing features are listed as context but not scheduled for implementation.
6. Pipeline pauses at `DESIGN_CHECKPOINT` for user review.
7. On `resume`, sprints execute for new features only.

---

## 3. The `sync` Command

### 3.1 CLI Interface

```bash
# Full sync — detect all drift, resolve interactively
agentic-dev sync my-app

# Sync from a specific source of truth
agentic-dev sync my-app --from code       # code is truth, update specs
agentic-dev sync my-app --from specs      # specs are truth, queue code changes
agentic-dev sync my-app --from figma      # Figma is truth, update specs

# Targeted sync — only check specific areas
agentic-dev sync my-app --scope api       # API contract vs actual endpoints
agentic-dev sync my-app --scope frontend  # frontend spec vs frontend code
agentic-dev sync my-app --scope backend   # backend spec vs backend code

# Check-only mode (report drift, no changes)
agentic-dev sync my-app --check
```

### 3.2 Execution Steps

1. **Analyze current state** — Run analysis agents in parallel against each source:
   - `code_analyzer` reads frontend code → current frontend snapshot (components, routes, patterns)
   - `code_analyzer` reads backend code → current backend snapshot (endpoints, models, services)
   - Spec Reader reads existing spec documents from `docs/`
   - Figma Analyzer (if Figma sources configured) re-reads current Figma state

2. **Produce drift report** — `drift_detector` agent receives all snapshots and specs, compares them, and produces a structured **Sync Report** (see Section 3.3).

3. **User resolution** — For each drift item, the user decides:
   - **to_spec** — update the spec document to match code/Figma reality
   - **to_code** — queue a code change to match the spec (becomes a sprint task)
   - **ignore** — mark as intentional divergence (stored in `sync_ignores`)
   - **defer** — skip for now, will reappear on next sync

4. **Apply resolutions:**
   - **Spec updates** (`to_spec` items) — `spec_updater` agent surgically modifies the relevant spec documents (not a full regeneration). Applied immediately within the sync operation.
   - **Code updates** (`to_code` items) — Collected into a change request document saved as `docs/sync_change_request.md`. Sync completes, state returns to `COMPLETE`/`ADOPTED`, then the user is prompted: `"N code changes queued. Run 'agentic-dev update <app> --from-sync' to apply them."` The `update --from-sync` flag reads the saved change request and feeds it into the standard update pipeline. This avoids circular state issues (sync doesn't internally invoke update while in SYNCING state).
   - **Feature sync** — `feature_extractor` updates `features.md` to reflect any added/removed/modified features from `to_spec` resolutions.

5. **Save state** — Transition back to previous terminal state (`COMPLETE` or `ADOPTED`). Update `last_sync_at`, persist any new `sync_ignores`. If `to_code` items exist, save the change request document.

### 3.3 Sync Report Format

```markdown
# Sync Report

## API Contract
### In code but not in spec
- [DRIFT-001] POST /api/v2/webhooks — found in backend/routes/webhooks.py
- [DRIFT-002] GET /api/users/:id/preferences — found in backend/routes/users.py

### In spec but not in code
- [DRIFT-004] DELETE /api/users/:id — specified in api_contract.md, no implementation found

### Differences
- [DRIFT-005] POST /api/auth/login response shape differs: spec says {token}, code returns {token, user}

## Frontend
### In code but not in spec
- [DRIFT-007] SettingsPage component — found at client/src/pages/Settings.tsx

## Figma vs Spec
### Design token drift
- [DRIFT-008] Primary color: Figma #3B82F6, spec #2563EB
```

### 3.4 The `--from` Shortcut

When `--from code` is specified, all drift items are auto-resolved as "to_spec" (code is truth, specs get updated). This includes `in_spec_not_code` items — they are removed from specs since the code doesn't have them.

When `--from specs`, all items are auto-resolved as "to_code" (specs are truth, code changes are queued).

When `--from figma`, Figma-vs-spec items are resolved as "to_spec" (Figma is truth for design tokens and components); code-vs-spec items are left for interactive resolution.

This skips interactive resolution for the resolved items.

### 3.5 The `--check-sync` Flag

Available on `update` and `resume` commands. Before those commands execute, a lightweight drift check runs:

- Compares file modification timestamps against `last_sync_at`
- If files in mapped code directories have changed since last sync, counts changed files and estimates drift magnitude
- If significant drift detected (>5 files changed or key config files modified), warns:
  ```
  Warning: Drift detected — 5 items out of sync (code has changed since last sync)
  Run `agentic-dev sync my-app` to resolve, or use --force to proceed anyway.
  ```
- With `--force`, the command proceeds without sync. Without it, the command aborts with the warning.

---

## 4. New Agents

### 4.1 `structure_detector`

| Property | Value |
|---|---|
| Purpose | Scan project for directory mapping (framework markers, package files) |
| Model | sonnet |
| Permission mode | plan (read-only) |
| Allowed tools | Read, Glob, Grep |
| Max turns | 20 |
| Input | Project root path |
| Output | Directory mapping proposal (JSON: frontend path, backend path, project type) |

The agent looks for framework markers: `package.json` with React/Vue/Angular/Svelte, `requirements.txt`/`pyproject.toml` with Django/Flask/FastAPI, `go.mod`, `Cargo.toml`, etc. It proposes which directories contain frontend vs backend code.

### 4.2 `spec_reverse_engineer`

| Property | Value |
|---|---|
| Purpose | Read code and produce a spec document in the pipeline's expected format |
| Model | opus |
| Permission mode | plan (read-only) |
| Allowed tools | Read, Glob, Grep, WebSearch |
| Max turns | 80 |
| Input | Code directory + target spec type (frontend_spec, backend_spec, or api_contract) |
| Output | Spec document in the exact format the Architect agent would produce |

This is the heaviest new agent. It must produce specs that match the Architect's output format exactly — including the `<!-- DOCUMENT: name -->` markers when producing multi-document output, the `[P001]`/`[M001]`/`[E001]` ID conventions, and all required sections.

The agent receives a system prompt with the target spec format (extracted from the Architect template) and instructions to reverse-engineer it from real code.

### 4.3 `feature_extractor`

| Property | Value |
|---|---|
| Purpose | Read specs and code, extract features with acceptance criteria |
| Model | opus |
| Permission mode | plan (read-only) |
| Allowed tools | Read, Glob, Grep |
| Max turns | 50 |
| Input | All spec documents + code directories |
| Output | `features.md` in the Feature Analyst's output format |

Produces features with `[EXISTING-F001]` IDs. Each feature has description, acceptance criteria (derived from what the code actually does), dependencies, and priority.

### 4.4 `code_analyzer`

| Property | Value |
|---|---|
| Purpose | Produce a structured snapshot of current code state for drift comparison |
| Model | sonnet |
| Permission mode | plan (read-only) |
| Allowed tools | Read, Glob, Grep |
| Max turns | 30 |
| Input | Code directory + analysis scope (frontend, backend, or api) |
| Output | Structured code reality snapshot (endpoints list, models list, components list, routes list) |

Lighter than `spec_reverse_engineer` — it produces a structured inventory rather than a full spec. The snapshot format is designed for comparison by the `drift_detector`.

### 4.5 `drift_detector`

| Property | Value |
|---|---|
| Purpose | Compare code reality snapshots against specs, produce sync report |
| Model | opus |
| Permission mode | plan (read-only) |
| Allowed tools | Read, Glob, Grep |
| Max turns | 40 |
| Input | Code snapshots + spec documents + Figma analysis (if available) |
| Output | Sync Report (structured markdown with `[DRIFT-nnn]` IDs) |

Does not read code directly — it receives pre-analyzed snapshots and compares them against spec documents. This separation keeps the agent focused on comparison logic.

### 4.6 `spec_updater`

| Property | Value |
|---|---|
| Purpose | Surgically update a specific spec document based on resolved drift items |
| Model | sonnet |
| Permission mode | plan (read-only for spec reading) |
| Allowed tools | Read, Glob, Grep |
| Max turns | 30 |
| Input | Current spec document + list of resolved drift items with their resolutions |
| Output | Updated spec document content |

Makes targeted edits to existing spec documents rather than regenerating from scratch. This preserves user customizations and is much cheaper than re-running the full architect.

### 4.7 QA Agents for Spec Generation

The existing system requires QA review for every agent that produces format-critical documents. Two new agents produce documents that must exactly match pipeline-expected formats and are high-risk for format violations:

**`spec_reverse_engineer_qa`**

| Property | Value |
|---|---|
| Purpose | Validate that reverse-engineered specs match the Architect's output format |
| Model | sonnet |
| Permission mode | plan (read-only) |
| Allowed tools | Read, Glob, Grep |
| Max turns | 20 |
| Input | Generated spec + target format reference |
| Output | QA report: format compliance, completeness, ID convention adherence |

Reviews for: `<!-- DOCUMENT: name -->` markers present (when required), `[P001]`/`[M001]`/`[E001]`/`[S001]` ID conventions followed, all required sections present, no empty sections, internal cross-references valid.

**`feature_extractor_qa`**

| Property | Value |
|---|---|
| Purpose | Validate that extracted features match the Feature Analyst's output format |
| Model | sonnet |
| Permission mode | plan (read-only) |
| Allowed tools | Read, Glob, Grep |
| Max turns | 20 |
| Input | Generated features document + format reference |
| Output | QA report: format compliance, acceptance criteria quality, ID uniqueness |

Reviews for: `[EXISTING-F001]` ID format, each feature has Description/Acceptance Criteria/Dependencies/Priority sections, acceptance criteria are specific and testable, no duplicate IDs.

Both QA agents use the standard QA cycle mechanism (`run_qa_cycle`). `structure_detector`, `code_analyzer`, `drift_detector`, and `spec_updater` do not need QA agents — they produce lightweight/operational output rather than format-critical pipeline documents.

### 4.8 Cost Considerations

Adoption of a medium-sized project runs multiple opus-model agents:
- `spec_reverse_engineer` x2-3 (frontend, backend, api_contract): ~$5-15 each
- `feature_extractor` x1: ~$3-5
- QA cycles: ~$1-3 each
- Estimated total: **$20-50 for a medium codebase**

The `adopt` command should display an estimated cost before proceeding and require user confirmation. The `--yes` flag skips confirmation for automation.

Sync is cheaper: `code_analyzer` (sonnet) + `drift_detector` (opus) typically costs **$2-5** per sync.

---

## 5. State Model Changes

### 5.1 PipelineState Changes

```python
class PipelineState(BaseModel):
    # Existing fields unchanged...
    mode: Literal["new", "update", "remediate", "adopt"]  # add "adopt"
    origin: Literal["created", "adopted"] = "created"     # how project was started
    last_sync_at: datetime | None = None                   # last successful sync timestamp
```

### 5.2 New PipelinePhase Values

```python
class PipelinePhase(str, Enum):
    # ... existing phases ...
    ADOPTING = "adopting"       # running spec reverse-engineering
    SYNCING = "syncing"         # running drift detection + resolution
    ADOPTED = "adopted"         # adoption complete, specs generated, no pipeline run yet
```

`ADOPTED` is distinct from `COMPLETE` because `COMPLETE` has semantic meaning: it means the full pipeline ran including UAT. An adopted project has specs but no `uat_report`, no sprint history, and has never been through the build pipeline. Commands that require `COMPLETE` (like `remediate`, which reads `uat_report`) will correctly reject `ADOPTED` projects. To use `remediate`, an adopted project must first go through at least one `update` cycle.

`ADOPTED` supports the same outgoing transitions as `COMPLETE`: it can transition to `INPUT_PROCESSING` (for `update`), `SYNCING` (for `sync`), or `ADOPTING` (for re-adoption with `--force`).

### 5.3 Phase Transitions

**Current state:** `VALID_TRANSITIONS` in `transitions.py` has `COMPLETE: []` and `FAILED: []` (no outgoing transitions). The existing `reset_for_update()` function bypasses `validate_transition()` entirely by directly assigning `state.phase`. This creates a dual-path system.

**Approach:** Unify on the validation path. Refactor `reset_for_update()` to use `validate_transition()` instead of direct assignment. Add all necessary outgoing transitions to `VALID_TRANSITIONS`:

```python
VALID_TRANSITIONS = {
    # ... existing transitions (keep all current entries) ...
    
    # Modified entries:
    PipelinePhase.IDLE: [PipelinePhase.INPUT_PROCESSING, PipelinePhase.ADOPTING],
    PipelinePhase.COMPLETE: [
        PipelinePhase.INPUT_PROCESSING,  # for update/remediate (currently bypassed)
        PipelinePhase.SYNCING,           # for sync
    ],
    PipelinePhase.FAILED: [
        PipelinePhase.INPUT_PROCESSING,  # for resume after failure (currently bypassed)
    ],
    
    # New entries:
    PipelinePhase.ADOPTING: [PipelinePhase.ADOPTED, PipelinePhase.INPUT_PROCESSING, PipelinePhase.FAILED],
    PipelinePhase.ADOPTED: [
        PipelinePhase.INPUT_PROCESSING,  # for update or --extend continuation
        PipelinePhase.SYNCING,           # for sync
    ],
    PipelinePhase.SYNCING: [PipelinePhase.COMPLETE, PipelinePhase.ADOPTED, PipelinePhase.FAILED],
}
```

`ADOPTING` can transition to `INPUT_PROCESSING` when `--extend` is used (adoption feeds into the standard pipeline). `reset_for_update()` must be refactored to call `validate_transition()` rather than directly assigning `state.phase`.

### 5.4 SyncReport Model

```python
class DriftItem(BaseModel):
    id: str                       # DRIFT-001, DRIFT-002, ...
    scope: Literal["api", "frontend", "backend", "figma"]
    category: Literal["in_code_not_spec", "in_spec_not_code", "difference", "design_drift"]
    description: str
    source_file: str | None       # where in code this was found
    spec_reference: str | None    # where in spec this is referenced
    resolution: Literal["to_spec", "to_code", "ignore", "defer"] | None = None

class SyncReport(BaseModel):
    generated_at: datetime
    scope: Literal["all", "api", "frontend", "backend"] = "all"  # which scope was checked
    items: list[DriftItem]
    summary: str                  # human-readable summary
```

### 5.5 Backwards Compatibility

- All new fields on `PipelineState` have defaults — existing `state.json` files deserialize without error.
- `config.json` requires migration (see Section 1.3) — old flat `CheckpointConfig` format is auto-detected and wrapped into `ProjectConfig` on load.
- Projects created before the global registry existed are resolved via `DEFAULT_PROJECTS_DIR / app_name` fallback (see Section 2.4).
- The `new` command behavior is 100% unchanged.
- `update` and `resume` work identically — `--check-sync` is opt-in.
- `remediate` requires `COMPLETE` state (not `ADOPTED`), which correctly prevents remediation on projects that haven't been through the build pipeline.

---

## 6. Changes to Existing Code

### 6.1 WorkspaceManager

- New `adopt_project(path)` method: creates `.agentic-dev/` and `docs/` in an existing directory (no `frontend/`/`backend/` creation).
- All path resolution uses `directory_map` from `ProjectConfig` instead of hardcoded `frontend/`/`backend/`.
- `create_project()` unchanged for `new` command but now also registers the project in the global registry.
- `get_project_dir()` updated to check the global registry first, falling back to `DEFAULT_PROJECTS_DIR / app_name`.

**Exhaustive list of hardcoded `"frontend"` / `"backend"` paths to refactor:**

| File | Function/Line | Current Hardcoded Path |
|---|---|---|
| `workspace/manager.py` | `create_code_dirs()` | `"frontend"`, `"backend"` directory creation |
| `orchestrator/engine.py` | `_run_input_processing()` | `"frontend"`, `"backend"` code dir creation |
| `orchestrator/engine.py` | `_setup_workspaces()` | `"frontend"`, `"backend"` for CLAUDE.md generation |
| `orchestrator/engine.py` | `_commit_sprint_changes()` | `"frontend"`, `"backend"` for git commit paths |
| `orchestrator/sprint_runner.py` | `_execute()` | `"backend"`, `"frontend"` working directory resolution |
| `workspace/claude_md.py` | various | `"frontend"`, `"backend"` in generated CLAUDE.md content |

All of these must resolve through `config.directory_map` instead.

### 6.2 Engine

- `_run_input_processing()` reads `directory_map` from config for code directory creation (only for `new` projects).
- `_setup_workspaces()` writes CLAUDE.md to mapped directories.
- New `_run_adoption()` method handles the ADOPTING phase.
- New `_run_sync()` method handles the SYNCING phase.

### 6.3 SprintRunner

- Resolves working directories from `directory_map` instead of hardcoded paths.
- `backend/` → `config.directory_map.backend`
- `frontend/` → `config.directory_map.frontend`

### 6.4 CLI

- New `adopt` command with `--from-figma`, `--extend`, `--frontend`, `--backend`, `--yes` options.
- New `sync` command with `--from`, `--scope`, `--check` options.
- `--check-sync` and `--force` flags added to `update`, `resume` commands.
- `--from-sync` flag added to `update` command — reads `docs/sync_change_request.md` as the change request instead of interactive input. This is how `to_code` sync resolutions are applied.
- `adopt` and `sync` commands resolve project paths through the global registry (Section 2.4).

### 6.5 Input Processor Template

- When processing `--extend` input: receives structured_input containing `[EXISTING-F...]` features plus new requirements. Distinguishes existing from new in its output.

### 6.6 Sprint Planner

- Understands `[EXISTING-F...]` features: lists them as context but only creates sprints for new/modified features (those without `EXISTING-` prefix).

### 6.7 What Stays the Same

- All QA agents (they review documents, not code structure).
- Feature Analyst (receives structured_input, same as always).
- Architect (for `new` projects, unchanged).
- The QA cycle mechanism.
- State machine core logic (advance_phase, resume_from_failure).
- Document store read/write/archive.

---

## 7. End-to-End Flows

### 7.1 Adopt an Existing Project

```
User: agentic-dev adopt /home/user/my-saas --from-figma "https://figma.com/file/abc::UI"

1. Validate /home/user/my-saas exists, no .agentic-dev/ present
2. Create .agentic-dev/, docs/ in-place
3. Register "my-saas" → /home/user/my-saas in ~/.agentic-dev/registry.json
4. structure_detector scans project → proposes: frontend=client, backend=api
5. User confirms mapping. Display cost estimate, user confirms.
6. In parallel:
   a. spec_reverse_engineer reads client/ → frontend_spec.md (QA-reviewed)
   b. spec_reverse_engineer reads api/ → backend_spec.md (QA-reviewed)
   c. Figma analyzer reads Figma URL → design_analyses.md
7. spec_reverse_engineer reads generated specs + code → api_contract.md (QA-reviewed)
   (incorporates Figma design tokens into frontend_spec)
8. feature_extractor reads all specs → features.md (QA-reviewed)
9. Produces structured_input.md
10. State → ADOPTED, origin=adopted, last_sync_at=now
11. Print summary: features extracted, endpoints mapped, pages documented
```

### 7.2 Adopt + Extend

```
User: agentic-dev adopt /home/user/my-saas --extend "Add admin dashboard"

1-9. Same as 7.1
10. State → INPUT_PROCESSING (transition from ADOPTING)
11. Input Processor receives structured_input + new requirements
12. Feature Analyst adds new features alongside existing
13. Architect updates specs for new features
14. Sprint Planner creates sprints for new features only
15. State → DESIGN_CHECKPOINT (pause for review)
16. User reviews → agentic-dev resume → sprints for new features
```

### 7.3 Sync After Manual Code Changes

```
User: agentic-dev sync my-saas

1. Resolve "my-saas" via registry → /home/user/my-saas
2. In parallel: code_analyzer reads client/ + api/ (from directory_map)
3. drift_detector compares snapshots vs specs → sync report
4. User resolves per-item: to_spec, to_code, ignore, defer
5. spec_updater updates specs for "to_spec" items
6. "to_code" items saved to docs/sync_change_request.md
7. feature_extractor updates features.md for spec changes
8. State returns to ADOPTED (or COMPLETE). last_sync_at → now
9. If to_code items exist: "3 code changes queued. Run 'agentic-dev update my-saas --from-sync' to apply."
```

### 7.4 Sync with Source-of-Truth Shortcut

```
User: agentic-dev sync my-saas --from code

1-3. Same as 7.3 (analysis + drift report)
4. All items auto-resolved as "to_spec" (code wins, including removing spec-only items)
5. spec_updater updates all spec documents to match code
6. last_sync_at → now
```

### 7.5 Check-Sync Before Update

```
User: agentic-dev update my-saas --check-sync
[prompted for input]: Add dark mode

1. Lightweight drift check: compare file mtimes vs last_sync_at
2. If drift detected:
   "Warning: 5 items out of sync. Run 'agentic-dev sync' or use --force."
   Command aborts.
3. If no drift (or --force): update proceeds normally
```

---

## 8. Relationship to Existing Onboarding

The current `--from-codebase` and `--from-figma` flags on `agentic-dev new` remain functional for lightweight onboarding scenarios (appending analysis context to user_input). They serve a different purpose:

- `new --from-codebase`: "I'm building something new, but here's an existing codebase for context."
- `adopt`: "I want agentic-dev to fully understand and manage this existing project."

The `adopt` command uses the same underlying `ClaudeRunner` infrastructure and similar agent patterns, but with purpose-built agents that produce full spec documents rather than analysis summaries.

Over time, `--from-codebase` on `new` could be deprecated in favor of `adopt --extend`, but this is not required for the initial implementation.

---

## 9. Testing Strategy

### 9.1 Unit Tests

- `ProjectConfig` model serialization/deserialization, defaults, backwards compat
- `DirectoryMap` path resolution
- `SyncReport` and `DriftItem` model validation
- State transitions for new phases (ADOPTING, SYNCING)
- `WorkspaceManager.adopt_project()` directory creation

### 9.2 Agent Tests (Mocked)

- `structure_detector` with various project layouts (React+Express, Django+Vue, monorepo, single-app)
- `spec_reverse_engineer` output format validation (must match Architect output format)
- `feature_extractor` output format validation (must match Feature Analyst output format)
- `code_analyzer` snapshot format consistency
- `drift_detector` with known drift scenarios (items in code not spec, items in spec not code, differences)
- `spec_updater` surgical edit verification

### 9.3 CLI Tests

- `adopt` command: validation, directory creation, flag parsing
- `sync` command: all flag combinations (`--from`, `--scope`, `--check`)
- `--check-sync` flag on update/resume
- Error cases: adopt on already-adopted project, sync on non-existent project

### 9.4 Integration Tests

- Full adopt flow: mock Claude responses, verify all spec documents produced
- Full sync flow: mock drift detection, verify spec updates applied
- Adopt + extend flow: verify pipeline transitions correctly to DESIGN_CHECKPOINT
- Backwards compatibility: existing project state loads without error with new fields
