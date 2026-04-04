# Crash Resilience: Sub-step Checkpointing, Signal Handling & Session Persistence

**Date:** 2026-04-04
**Status:** Draft (v3 — revised after two spec reviews)

## Problem

When a pipeline run crashes mid-execution (OOM, network failure, Ctrl+C, SIGTERM), the system loses all progress within the currently-executing phase. For sprint phases — which run sequential backend, frontend, and integration QA cycles — this means completed sub-steps are re-run from scratch on resume, wasting both time and API cost.

The existing `resume` command restarts from the last *phase* boundary, but has no sub-step granularity. Additionally, there is no signal handling for graceful shutdown, and Claude session IDs are not persisted across process restarts.

## Goals

1. **Sub-step checkpointing:** Save state after each sprint sub-step (backend dev, frontend dev, integration) so completed work survives crashes.
2. **Graceful signal handling:** Catch SIGINT/SIGTERM, save current state, and exit cleanly.
3. **Best-effort session resume:** Persist Claude session IDs to state so crashed agent runs can attempt `--resume` on restart.

## Non-Goals

- Mid-agent checkpointing (saving partial output from a running Claude invocation)
- Automatic crash detection and restart (daemon/watchdog)
- Retry policies for non-rate-limit errors
- Resuming QA agent sessions (only action agent sessions are persisted — QA is cheap to re-run)

## Known Limitations

- A crash *during* a sub-step (while an agent is running) loses that sub-step's work. The sub-step re-runs from scratch on resume.
- A crash between sub-step completion and the `state_manager.save()` call means the sub-step re-runs. The window is tiny (microseconds) but nonzero.
- Session resume is best-effort — Claude sessions can expire or become invalid. The system always falls back to a clean restart.

## Design

### 1. State Model Changes

**File:** `src/agentic_dev/state/models.py`

Add two fields:

```python
class SprintState(BaseModel):
    # ... existing fields (backend_session_id, frontend_session_id already exist) ...
    integration_session_id: str | None = None  # NEW
    failed_at_step: SprintStatus | None = None  # NEW — records which sub-step failed

class PipelineState(BaseModel):
    # ... existing fields ...
    active_session_id: str | None = None  # NEW — for non-sprint phase session resume
```

**Why `failed_at_step`?** Currently `engine.py:351` sets `sprint.status = SprintStatus.FAILED` on failure, overwriting whatever sub-step status was in progress. Without a separate field, we lose track of which sub-step failed. `failed_at_step` records the sub-step that was active when the failure occurred, so `resume_from_failure` can restore it.

**Backward compatibility:** All new fields default to `None`. Existing `state.json` files load without error.

Existing unused fields `backend_session_id` and `frontend_session_id` on `SprintState` will now be actively populated.

### 2. Signal Handling

**New file:** `src/agentic_dev/orchestrator/shutdown.py`
**Modified files:** `src/agentic_dev/orchestrator/engine.py`, `src/agentic_dev/cli.py`, `src/agentic_dev/exceptions.py`

#### Shutdown Module

```python
# src/agentic_dev/orchestrator/shutdown.py
import asyncio
import signal

_shutdown_event: asyncio.Event | None = None

def get_shutdown_event() -> asyncio.Event:
    """Return the singleton shutdown event, creating it if needed."""
    global _shutdown_event
    if _shutdown_event is None:
        _shutdown_event = asyncio.Event()
    return _shutdown_event

def install_signal_handlers() -> None:
    """Install SIGINT/SIGTERM handlers on the running event loop.

    Must be called from within an async context (i.e., inside a running loop).
    """
    loop = asyncio.get_running_loop()
    event = get_shutdown_event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, event.set)
```

**Key detail:** `install_signal_handlers()` uses `asyncio.get_running_loop()`, NOT a pre-fetched loop object. This is because `cli.py` uses `asyncio.run(engine.run())` at line 209, which creates its own loop internally. Signal handlers must be installed *inside* the running loop — so `engine.run()` calls `install_signal_handlers()` at the start.

#### New Exception

```python
# In src/agentic_dev/exceptions.py
class GracefulShutdown(AgenticDevError):
    """Raised when a shutdown signal (SIGINT/SIGTERM) is received."""
    def __init__(self, phase: PipelinePhase) -> None:
        self.phase = phase
        super().__init__(f"Graceful shutdown at phase: {phase}")
```

Uses `PipelinePhase` (not `str`) for type safety, consistent with `CheckpointPause`.

#### Engine Integration

In `PipelineEngine.run()`, install signal handlers and check the shutdown event at the top of each loop iteration:

```python
async def run(self) -> None:
    install_signal_handlers()
    state = self._state_manager.load()
    shutdown_event = get_shutdown_event()

    while state.phase not in (PipelinePhase.COMPLETE, PipelinePhase.FAILED):
        if shutdown_event.is_set():
            self._state_manager.save(state)
            raise GracefulShutdown(phase=state.phase)

        try:
            state = await self._execute_phase(state)
        except (AgentRunError, OutputParseError) as exc:
            # ... existing error handling ...
```

#### Sprint Runner Shutdown Check

The sprint runner's `_execute_sprint` also checks the shutdown event between sub-steps. This is important because the SPRINTING phase can run for hours across multiple sub-steps — checking only between *phases* would mean a very long wait:

```python
async def _execute_sprint(self, ...) -> SprintResult:
    shutdown_event = get_shutdown_event()

    # Backend
    if self._has_backend and not _should_skip(sprint.status, SprintStatus.BACKEND_DEV):
        if shutdown_event.is_set():
            self._state_manager.save(self._pipeline_state)
            raise GracefulShutdown(phase=PipelinePhase.SPRINTING)
        # ... run backend QA cycle ...

    # Frontend
    if self._has_frontend and not _should_skip(sprint.status, SprintStatus.FRONTEND_DEV):
        if shutdown_event.is_set():
            self._state_manager.save(self._pipeline_state)
            raise GracefulShutdown(phase=PipelinePhase.SPRINTING)
        # ... run frontend QA cycle ...
```

#### CLI Integration

Catch `GracefulShutdown` in `_run_pipeline`. **Important:** Place `except GracefulShutdown` between the existing `except CheckpointPause` (line 220) and `except AgenticDevError` (line 231) handlers. Since `GracefulShutdown` extends `AgenticDevError`, placing it after the `AgenticDevError` handler would cause it to be caught by the broader handler instead.

```python
except CheckpointPause:
    # ... existing checkpoint handler (lines 220-230) ...
except GracefulShutdown:  # NEW — must come BEFORE AgenticDevError
    current_state = state_manager.load()
    teardown_logging()
    console.print("[yellow]Shutdown requested. State saved.[/yellow]")
    console.print(f"  Resume with: agentic-dev resume {app_name}")
except AgenticDevError as exc:
    # ... existing error handler (lines 231-242) ...
```

**Timing:** Signals are caught *between* agent invocations, not during. A running Claude subprocess finishes its current call first. For hard kills (SIGKILL), atomic state writes protect the last completed sub-step.

### 3. Sub-step Checkpointing in Sprint Runner

**Files:** `src/agentic_dev/orchestrator/sprint_runner.py`, `src/agentic_dev/orchestrator/engine.py`

#### SprintRunner Interface Changes

`SprintRunner.__init__` gains two new parameters:

```python
def __init__(
    self, ...,
    state_manager: StateManager,
    pipeline_state: PipelineState,
) -> None:
    self._state_manager = state_manager
    self._pipeline_state = pipeline_state
```

`run_sprint` gains a `sprint_state` parameter so it can read/update sub-step status:

```python
async def run_sprint(
    self,
    sprint_number: int,
    sprint_scope: str,
    sprint_state: SprintState,  # NEW — the SprintState object for this sprint
    needs_integration: bool = False,
) -> SprintResult:
```

`_execute_sprint` gains the same `sprint_state` parameter, threaded from `run_sprint`.

#### Engine Changes to `_run_sprints`

Currently `engine.py:331` unconditionally sets `sprint.status = SprintStatus.BACKEND_DEV` for every non-COMPLETE sprint. This **destroys sub-step progress on resume**. Fix:

```python
# BEFORE (current code):
sprint.status = SprintStatus.BACKEND_DEV

# AFTER:
if sprint.status == SprintStatus.PENDING:
    sprint.status = SprintStatus.BACKEND_DEV
```

Also update the `sprint_runner.run_sprint()` call to pass `sprint_state=sprint`, and `_get_sprint_runner` to pass `state_manager=self._state_manager, pipeline_state=state`.

#### Skip Logic

The `SprintStatus` enum has 11 values. The skip logic must handle all of them. Define a step ordering that groups QA/correction sub-statuses with their parent step:

```python
# In sprint_runner.py

# Maps each SprintStatus to its "phase group" index for skip comparison.
# Sub-steps within the same QA cycle (dev -> qa -> correction) share a group
# because the QA cycle is atomic — if any part fails, the whole cycle re-runs.
_STEP_GROUP: dict[SprintStatus, int] = {
    SprintStatus.PENDING: 0,
    SprintStatus.BACKEND_DEV: 1,
    SprintStatus.BACKEND_QA: 1,
    SprintStatus.BACKEND_CORRECTION: 1,
    SprintStatus.FRONTEND_DEV: 2,
    SprintStatus.FRONTEND_QA: 2,
    SprintStatus.FRONTEND_CORRECTION: 2,
    SprintStatus.INTEGRATION: 3,
    SprintStatus.INTEGRATION_QA: 3,
    SprintStatus.INTEGRATION_CORRECTION: 3,
    SprintStatus.COMPLETE: 4,
    SprintStatus.FAILED: 0,  # FAILED with failed_at_step handles resume
}

def _should_skip(current_status: SprintStatus, step: SprintStatus) -> bool:
    """Return True if ``step`` was already completed based on ``current_status``."""
    return _STEP_GROUP[current_status] > _STEP_GROUP[step]
```

**Rationale for grouping QA/correction with dev:** The QA cycle (`run_qa_cycle`) is an atomic unit — action agent + QA review + optional corrections. If a crash happens mid-QA (e.g., during backend_qa or backend_correction), the entire backend QA cycle re-runs. This matches the existing `run_qa_cycle` design which has no internal checkpointing.

**Interaction with project-type skip logic:** The skip logic composes correctly with `self._has_backend` / `self._has_frontend` because both conditions are checked:

```python
if self._has_backend and not _should_skip(sprint_state.status, SprintStatus.BACKEND_DEV):
    # run backend
```

A `backend_only` project with `self._has_frontend = False` will never attempt frontend regardless of skip logic.

#### Sub-step State Saves in `_execute_sprint`

```python
async def _execute_sprint(
    self,
    sprint_number: int,
    sprint_scope: str,
    sprint_state: SprintState,
    needs_integration: bool,
    partial_cost: list[float],
) -> SprintResult:
    # ... existing setup (read specs, extra_context) ...

    # Backend QA cycle
    if self._has_backend and not _should_skip(sprint_state.status, SprintStatus.BACKEND_DEV):
        sprint_state.status = SprintStatus.BACKEND_DEV
        self._state_manager.save(self._pipeline_state)

        backend_result = await run_qa_cycle(...)
        sprint_state.backend_session_id = backend_result.session_id
        partial_cost[0] += backend_result.total_cost

        # Advance status past backend
        sprint_state.status = SprintStatus.FRONTEND_DEV if self._has_frontend else (
            SprintStatus.INTEGRATION if needs_integration else SprintStatus.COMPLETE
        )
        self._state_manager.save(self._pipeline_state)

    # Frontend QA cycle
    if self._has_frontend and not _should_skip(sprint_state.status, SprintStatus.FRONTEND_DEV):
        sprint_state.status = SprintStatus.FRONTEND_DEV
        self._state_manager.save(self._pipeline_state)

        frontend_result = await run_qa_cycle(...)
        sprint_state.frontend_session_id = frontend_result.session_id
        partial_cost[0] += frontend_result.total_cost

        sprint_state.status = SprintStatus.INTEGRATION if needs_integration else SprintStatus.COMPLETE
        self._state_manager.save(self._pipeline_state)

    # Integration QA cycle
    if needs_integration and not _should_skip(sprint_state.status, SprintStatus.INTEGRATION):
        sprint_state.status = SprintStatus.INTEGRATION
        self._state_manager.save(self._pipeline_state)

        integration_result = await run_qa_cycle(...)
        sprint_state.integration_session_id = integration_result.session_id
        partial_cost[0] += integration_result.total_cost

        sprint_state.status = SprintStatus.COMPLETE
        self._state_manager.save(self._pipeline_state)

    return SprintResult(...)
```

### 4. Session ID Propagation Through QA Cycle

**File:** `src/agentic_dev/orchestrator/qa_cycle.py`

#### `QACycleResult` Change

Add `session_id` to capture the action agent's session:

```python
@dataclass(frozen=True)
class QACycleResult:
    # ... existing fields ...
    session_id: str | None = None  # NEW — action agent's session ID
```

#### `run_qa_cycle` Changes

1. Accept optional `session_id` parameter for action agent resume
2. Forward it to `_run_with_empty_retry` → `claude.run()`
3. Capture action agent's session ID on the result

```python
async def run_qa_cycle(
    ...,
    session_id: str | None = None,  # NEW — for resuming the action agent
) -> QACycleResult:
    # ...
    action_result = await _run_with_empty_retry(
        claude=claude,
        agent_config=action_config,
        prompt=action_prompt,
        workspace=workspace,
        agent_name=action_agent.name,
        error_message="Agent returned empty output",
        sprint=sprint,
        session_id=session_id,  # NEW — forwarded
    )
    # ... rest unchanged ...

    result = QACycleResult(
        output=latest_output,
        initial_qa_report=initial_qa_report,
        final_qa_report=final_qa_report,
        corrections=corrections,
        action_cost=action_result.cost_usd,
        initial_qa_cost=initial_qa_cost,
        session_id=action_result.session_id,  # NEW — captured from ClaudeResult
    )
    return result
```

#### `_run_with_empty_retry` Changes

Accept and forward `session_id`:

```python
async def _run_with_empty_retry(
    ...,
    session_id: str | None = None,  # NEW
) -> ClaudeResult:
    result = await claude.run(
        agent=agent_config, prompt=prompt, working_dir=workspace,
        session_id=session_id,  # NEW — forwarded to ClaudeRunner
    )
    # ... retry loop (retries don't use session_id — only the first attempt) ...
```

### 5. Best-Effort Session Resume Fallback

**File:** `src/agentic_dev/orchestrator/sprint_runner.py`

When resuming with a saved session ID, if the session is stale (expired, invalid), catch the error and retry without it. This logic lives in `_execute_sprint` around each `run_qa_cycle` call:

```python
# Example for backend:
saved_sid = sprint_state.backend_session_id
try:
    backend_result = await run_qa_cycle(..., session_id=saved_sid)
except AgentRunError:
    if saved_sid is not None:
        # Session stale — clear and retry without resume
        sprint_state.backend_session_id = None
        self._state_manager.save(self._pipeline_state)
        backend_result = await run_qa_cycle(..., session_id=None)
    else:
        raise  # Not a session issue — propagate
```

### 6. Resume Logic Update

**File:** `src/agentic_dev/state/transitions.py`

#### Current Behaviour (Broken for Sub-steps)

1. `engine.py:351-352` sets `sprint.status = SprintStatus.COMPLETE if result.success else SprintStatus.FAILED`
2. This overwrites whatever sub-step status the sprint runner had set (e.g., `FRONTEND_DEV`)
3. `resume_from_failure` resets FAILED sprints to `PENDING`
4. All sub-step progress lost

#### New Behaviour

1. Before marking FAILED, capture the current sub-step status in `sprint.failed_at_step`
2. `resume_from_failure` restores FAILED sprints to their `failed_at_step` (not `PENDING`)
3. The sprint runner's skip logic skips completed sub-steps on the resumed run

**Replace `engine.py` lines 351-370** (the post-sprint-runner block inside the `for sprint in state.sprints` loop) with:

```python
            # --- Replace current lines 351-370 ---
            sprint.completed_at = datetime.now(timezone.utc)
            state.total_cost_usd += result.total_cost

            if result.success:
                sprint.status = SprintStatus.COMPLETE
                await self._commit_sprint_changes(state, sprint)
            else:
                # Capture which sub-step was active BEFORE overwriting with FAILED.
                # At this point sprint.status still reflects the sub-step set by
                # SprintRunner (e.g., FRONTEND_DEV) because the runner only advances
                # status after a successful QA cycle.
                sprint.failed_at_step = sprint.status
                sprint.status = SprintStatus.FAILED

            self._state_manager.save(state)

            if should_pause(
                PipelinePhase.SPRINTING,
                self._checkpoint_config,
                sprint_just_completed=True,
            ):
                raise CheckpointPause(phase=PipelinePhase.SPRINTING)

            if not result.success:
                state.failed_at_phase = PipelinePhase.SPRINTING
                state.error = f"Sprint {sprint.sprint_number} failed"
                return advance_phase(state, PipelinePhase.FAILED)
```

**Key detail:** The `sprint.failed_at_step = sprint.status` line captures the sub-step *before* overwriting `sprint.status` with `SprintStatus.FAILED`. By this point, `sprint.status` still reflects the sub-step that was in progress when the error occurred (e.g., `FRONTEND_DEV`), because `SprintRunner._execute_sprint` only advances status *after* a successful QA cycle.

Updated `resume_from_failure`:

```python
def resume_from_failure(state: PipelineState) -> PipelineState:
    if state.phase != PipelinePhase.FAILED:
        raise InvalidTransitionError(state.phase, PipelinePhase.FAILED)

    target_phase = state.failed_at_phase or PipelinePhase.IDLE
    state.phase = target_phase
    state.error = None
    state.failed_at_phase = None

    for sprint in state.sprints:
        if sprint.status == SprintStatus.FAILED:
            # Restore to the sub-step where failure occurred
            sprint.status = sprint.failed_at_step or SprintStatus.PENDING
            sprint.failed_at_step = None
            sprint.completed_at = None

    state.updated_at = datetime.now(timezone.utc)
    return state
```

### 7. Non-Sprint Phase Session Resume

**File:** `src/agentic_dev/orchestrator/engine.py`

For phases like `FEATURE_ANALYSIS` and `ARCHITECTURE`, save the session ID to `state.active_session_id` and pass it through to `run_qa_cycle`:

```python
async def _run_feature_analysis(self, state: PipelineState) -> PipelineState:
    result = await run_qa_cycle(
        ...,
        session_id=state.active_session_id,  # NEW
    )
    state.active_session_id = result.session_id  # NEW — save for potential resume
    # ... rest unchanged ...
    state.active_session_id = None  # Clear on successful phase completion
    return advance_phase(state, PipelinePhase.FEATURE_ANALYSIS_QA)
```

Same pattern for `_run_architecture` and `_run_sprint_planning`. Note: `_run_uat` uses `_run_single_agent` (not `run_qa_cycle`), so session resume is not applicable there.

On resume, the phase re-runs with the saved session ID. If it fails, the best-effort fallback in each phase handler clears and retries (same pattern as sprint runner).

## Files to Modify

| File | Change |
|------|--------|
| `src/agentic_dev/state/models.py` | Add `integration_session_id`, `failed_at_step` to SprintState; `active_session_id` to PipelineState |
| `src/agentic_dev/orchestrator/shutdown.py` | **New file** — shutdown event and signal handler installation |
| `src/agentic_dev/exceptions.py` | Add `GracefulShutdown` exception |
| `src/agentic_dev/cli.py` | Catch `GracefulShutdown`, display resume instructions |
| `src/agentic_dev/orchestrator/engine.py` | Install signal handlers, check shutdown event, conditional status set in `_run_sprints`, pass state_manager/state to sprint runner, session ID threading for non-sprint phases |
| `src/agentic_dev/orchestrator/sprint_runner.py` | Accept state_manager/pipeline_state/sprint_state, sub-step checkpointing, skip logic, shutdown check between sub-steps, session ID persistence, best-effort fallback |
| `src/agentic_dev/orchestrator/qa_cycle.py` | Add `session_id` param to `run_qa_cycle` and `_run_with_empty_retry`, propagate to `claude.run()`, capture on `QACycleResult` |
| `src/agentic_dev/state/transitions.py` | Update `resume_from_failure` to restore `failed_at_step` instead of resetting to PENDING |

### Test Files to Modify/Create

| File | Change |
|------|--------|
| `tests/test_state/test_models.py` | New fields serialize/deserialize, backward compat |
| `tests/test_state/test_transitions.py` | Update `resume_from_failure` tests: verify sub-step preservation via `failed_at_step` |
| `tests/test_orchestrator/test_sprint_runner.py` | Skip logic tests for all 11 SprintStatus values; sub-step checkpointing; session ID round-trip; best-effort fallback |
| `tests/test_orchestrator/test_engine.py` | Conditional status set in `_run_sprints`; shutdown event check; non-sprint session ID threading |
| `tests/test_orchestrator/test_shutdown.py` | **New file** — signal handler installation, shutdown event |
| `tests/test_orchestrator/test_qa_cycle.py` | Session ID forwarding and capture |

## Testing Strategy

### Unit Tests

1. **State model** — new fields default to None; existing state.json without new fields loads correctly
2. **Skip logic** — test `_should_skip()` for every `SprintStatus` value: PENDING skips nothing, BACKEND_QA skips nothing (still in backend group), FRONTEND_DEV skips backend, COMPLETE skips everything, FAILED with `failed_at_step=FRONTEND_DEV` skips backend after resume
3. **Resume logic** — `resume_from_failure` with a FAILED sprint that has `failed_at_step=FRONTEND_DEV` results in `status=FRONTEND_DEV`; `failed_at_step=None` falls back to PENDING
4. **Conditional status set** — engine's `_run_sprints` only sets BACKEND_DEV when status is PENDING
5. **Signal handling** — `install_signal_handlers()` registers handlers on the running loop; `GracefulShutdown` raised when event is set
6. **Session ID propagation** — `run_qa_cycle` forwards session_id to runner, captures it on result
7. **Best-effort fallback** — stale session triggers retry without session_id

### Integration Tests

1. **Sub-step checkpoint round-trip** — run a sprint, mock crash after backend completes (status=FRONTEND_DEV), resume, verify backend QA cycle NOT called, frontend runs
2. **Signal handling end-to-end** — set shutdown event mid-sprint, verify state saved and GracefulShutdown raised
3. **Session ID persistence** — complete a sprint sub-step, verify session_id in state.json, resume with it

### Backward Compatibility

All new fields default to `None`. The skip logic treats `PENDING` as "nothing completed" and `failed_at_step=None` falls back to PENDING — identical to current behaviour. Existing `state.json` files work without migration.
