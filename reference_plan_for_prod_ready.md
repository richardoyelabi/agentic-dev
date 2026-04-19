# Plan: Stabilize & Production-Harden Behavior Intervention

## Context

[skillsbloom](.) is a monorepo containing two git subtrees: [backend/](backend/) (Django 5.2 + DRF, PostgreSQL, pytest) and [frontend/](frontend/) (Next.js 15, React 19, Vitest). It ships two product verticals:

- **Career readiness** — older, human-tested, believed stable (reference for conventions).
- **Behavior intervention (BI)** — built Sprints 2–9 by autonomous AI agents. Has unit tests that agents report as passing, but **no human has exercised it**, the FE and BE were implemented independently against hand-written DTOs (no shared schema), and the two halves have never been integrated or E2E-tested together.

The goal is to reach a point where BI can be turned on in production with confidence: environments run, the FE↔BE contract holds, known risk areas are covered by tests, manual walkthroughs have shaken out UX bugs, and CI enforces the bar going forward.

### What's in place today

- Backend BI: 8 sub-apps (`students`, `programs`, `sessions`, `observations`, `notes`, `signatures`, `ai`, `reports`), 21 test files (~5,890 LOC). **[behavior_intervention/reports/tests/](backend/django_server/behavior_intervention/reports/tests/) contains only `__init__.py` — zero tests for the entire Reports feature (F052).**
- Frontend BI: ~35 components across 9 sprints, but tests are limited to **6 React-Query-hook files**. Zero component tests for `SessionNoteForm`, `ObservationModal`, `SignaturePad`, `TipTapEditor`, `TemplateEditorModal`, route pages, or error boundaries.
- CI: only [frontend/.github/workflows/ci.yml](frontend/.github/workflows/ci.yml) exists and runs lint + build only (no tests, no backend). No root-level CI.
- No E2E harness for the full stack. The only "e2e" tests are Firebase-emulator-backed backend tests for `remote_learning` (career readiness).
- No shared schema (OpenAPI/tRPC/GraphQL). DTOs in [frontend/src/services/behavior-intervention.dto.ts](frontend/src/services/behavior-intervention.dto.ts) are hand-maintained against DRF serializers — high drift risk.
- Known fragile areas documented in [backend/CLAUDE.md](backend/CLAUDE.md): session state machine, `SIGNATURE_ENCRYPTION_KEY`, AI PHI exclusion, closed-session enforcement at view layer only, UUID-in-JSONField stringification, choice-set slug→field pinning.

## Phase 0 — Environment & smoke

Bring both stacks up and confirm the reported-green unit tests are actually green on this machine before writing anything new.

- Create `.venv/` at `backend/` root, install `requirements.txt`, copy `django_server/.env.dist` → `.env`, set `DATABASE_URL=sqlite:///db.sqlite3`, `SIGNATURE_ENCRYPTION_KEY`, `FIELD_ENCRYPTION_KEY`, `OPENAI_API_KEY` (test value), run `python manage.py migrate`, then `./scripts/pytest.sh` from the backend root. Capture the real baseline: pass/fail count by app, skips, warnings, slowest tests.
- In `frontend/`: `yarn install`, create `.env.local` with `NEXT_PUBLIC_BASE_URL=http://localhost:8000`, run `yarn test` and `yarn lint && yarn build`. Capture baseline.
- Start both dev servers (`python manage.py runserver` on :8000, `yarn dev` on :3005), log in, and click through one career-readiness flow and one BI flow to confirm the end-to-end happy path renders. This is the last thing we do before writing tests — anything that obviously breaks gets captured as a ticket, not fixed yet.
- Write a root-level `docs/DEV_SETUP.md` that codifies exactly what worked, including which `.env` keys are required for which subsystems (signature encryption, Firebase emulator opt-in, OpenAI, S3 opt-in, boto3 fallback).

Deliverable: a baseline report (`.omc/baseline.md`) — every subsequent phase must not regress it.

## Phase 1 — Contract verification (highest leverage)

The BE and FE were built independently. The single most likely source of bugs is DTO drift. Lock the contract first so later bug-hunting isn't whack-a-mole.

- Install `drf-spectacular` in the backend, wire the schema endpoint, and commit the generated OpenAPI document to the repo at `backend/django_server/openapi.yaml`. Add a pytest `integration`-marked test that regenerates the schema and diffs it against the committed file — any drift fails CI.
- Generate frontend types from the OpenAPI file using `openapi-typescript` into `frontend/src/services/generated/api-types.ts`. Do **not** replace `behavior-intervention.dto.ts` yet (that is a larger refactor). Instead, add a type-only compatibility test (`dto-contract.test-d.ts` using `tsd` / `expectTypeOf`) that asserts each hand-written DTO is assignable from the generated type. This surfaces every field-name and type mismatch without invasive changes.
- Triage the mismatches the type test flags. Likely hits (flagged in exploration):
  - `Observation.duration` (DRF `DurationField` → HH:MM:SS string) — verify round-trip.
  - `Observation.rate` (Decimal 10,4) vs `number` — confirm precision doesn't corrupt.
  - `SessionDTO.missing_signatures` enum vs. backend derivation in [sessions/serializers.py::_compute_missing_signatures](backend/django_server/behavior_intervention/sessions/serializers.py).
  - `Signature.signature_url` shape (data URI vs. S3 signed URL branching).
  - `TaskAnalysisResponseDTO` (FE expects both `step` UUID and `step_name` — confirm backend provides both).
- Each mismatch either gets a backend serializer fix, a frontend DTO fix, or a documented conversion layer — whichever is smaller. Every fix ships with a test.

## Phase 2 — Backend test gap-fill

Reference patterns to imitate: [career_readiness/dashboard/tests/test_student_endpoints.py](backend/django_server/career_readiness/dashboard/tests/test_student_endpoints.py) (API structure), [career_readiness/dashboard/tests/conftest.py](backend/django_server/career_readiness/dashboard/tests/conftest.py) (fixtures). Use `APIClient.force_authenticate`, `@pytest.mark.django_db`, plain pytest fixtures (no factory_boy), `seeded_choice_sets` fixture for any notes test.

### 2a. Reports sub-app (F052) — currently zero tests

Create `backend/django_server/behavior_intervention/reports/tests/` with:

- `test_models.py` — `ReportTemplate` default-immutability, `Report` date-range validation (start ≤ end), TipTap JSONField round-trip, public_id generation.
- `test_template_views.py` — full CRUD for E052–E056 (`ReportTemplateListView`, detail, create, update, delete). Cover: system-default templates cannot be mutated/deleted, `IsBCBAOrAdminPermission` enforcement, `PlatformFilterMixin` school scoping, pagination.
- `test_report_views.py` — E046–E050. Cover: student-scoped list, creation copies template snapshot, update persists TipTap document, delete is soft/hard (match implementation), closed-session constraint if any.
- `test_template_data_view.py` — E051. Build a student with 2 goals × 2 targets × N observations spanning the requested date range, assert the aggregated payload matches the expected per-collection-type aggregation (reuse `ProgressAggregationService`).
- `test_report_data_service.py` — dynamic-section rendering for each section `data_source` type; missing-data and date-range-empty edge cases.

### 2b. Cross-cutting integration tests (mark `@pytest.mark.integration`)

Create `backend/django_server/behavior_intervention/tests/test_integration_*.py`:

- `test_session_lifecycle.py` — full flow: create session → record observations (all 6 types) → create note → capture 3 signatures → generate AI narrative (mock LLM) → close session → attempt every mutation and assert 403 `SessionClosedError` on each. This is the most important regression guard.
- `test_platform_access.py` — user with BI-disabled school receives 403 on every BI endpoint; user with BI-enabled school can reach their school's data only; `user_schools_for_platform` returns expected queryset.
- `test_ai_phi_exclusion.py` — promote the existing PHI-exclusion asserts in `ai/tests/test_services.py` into a parameterized guard so every new field added to `SessionNote` or `StudentInfo` forces a conscious decision. Assert `_gather_data()` output JSON does not contain name, DOB, bio, address, diagnosis codes, or `others_present`.
- `test_signature_encryption.py` — write a signature, read raw DB column, confirm ciphertext != plaintext; round-trip via ORM returns original; mode switch (draw→upload) deletes old S3 key exactly once; `SIGNATURE_ENCRYPTION_KEY` override vs. `FIELD_ENCRYPTION_KEY` fallback is respected.
- `test_choice_set_slugs.py` — every slug in `_UUID_FIELD_SLUGS` resolves to a seeded `ChoiceSet`; cross-set options are rejected; inactive options are rejected.

### 2c. Existing-test audit

Not every "passing" test is load-bearing. Spot-check the 21 BI test files for: assertions that only check truthiness, tests that over-mock their subject, tests that pass because they don't actually run the code they claim to cover. Tighten in place — don't rewrite wholesale.

## Phase 3 — Frontend test gap-fill

Reference patterns: [frontend/src/app/behavior-intervention/programs/components/PhaseBadge/PhaseBadge.test.tsx](frontend/src/app/behavior-intervention/programs/components/PhaseBadge/PhaseBadge.test.tsx), the existing `*-fetch.test.tsx` files, and [frontend/src/app/behavior-intervention/observations/utils/validation.test.ts](frontend/src/app/behavior-intervention/observations/utils/validation.test.ts) (already strong — use as model).

### 3a. Core component tests (co-located `.test.tsx`)

Prioritize components with branching logic, not presentational ones.

- `SessionNoteForm` — field visibility matrix per note_type (97153 / 97151 / 97155 / 97156), submit strips hidden fields, multi-select vs single-select rendering, AI button disabled state when `aiNarrativeEnabled=false`, generation success writes to `narrative` via `setValue`.
- `ObservationModal` — one test suite per collection type (frequency, duration, rate, interval, trial, task-analysis), multi-row add/remove, per-row validation surfaces, `buildObservationPayload` strips irrelevant fields.
- `SignaturePad` — all three modes (Draw/Type/Upload); draw canvas exports PNG blob; type mode ships both PNG blob and `typed_text`; upload rejects >5MB and non-PNG/JPG; Clear/Remove/Save controls.
- `SignatureCaptureSection` — fetches signatures once, decides create-vs-update per slot, error boundary isolates a single pad failure.
- `SessionOverviewForm` — state-machine guard: forward transitions only; closed session redirects away from edit page.
- `TipTapEditor` — toolbar commands mutate content; imperative `insertContent` / `getJSON` work via ref; save-status indicator reflects mutation state.
- `TemplateEditorModal` — create/edit/clone modes, section add/remove, `cloneFrom` pre-populates, system-default badge disables edit/delete buttons.
- Route error boundaries: [behavior-intervention/error.tsx](frontend/src/app/behavior-intervention/error.tsx) and `reports/error.tsx` — render a thrown error, assert fallback UI.

### 3b. Page-level tests

Use `@testing-library/react` with `QueryClientProvider` and MSW (`msw` + `msw/node`) to stub the backend. Cover the one critical path per page:

- Student grid: search debounce + pagination → MSW returns paged data → correct URL params asserted.
- Session edit page: loads session, note, signatures; Save wires through; closed session redirects to detail page.
- Report editor: loads document, auto-save fires on idle, PDF download button triggers lazy `report-pdf.tsx` import (just assert the effect fires).
- Admin-only pages (`/settings/choice-sets`, `/settings/report-templates`): non-admin redirect, admin renders.

## Phase 4 — E2E harness

This is what has never existed. It's the only way to prove FE and BE actually integrate.

- Add `docker-compose.e2e.yml` at the repo root: Postgres, Django backend, Next.js frontend (built, not dev), localstack S3, and an OpenAI mock (a tiny Flask/FastAPI shim that returns canned narratives so tests are deterministic and free). Firebase emulator only if an E2E test actually touches `remote_learning`.
- Install Playwright at the repo root (or under `frontend/` — whichever integrates more cleanly). Config pointed at `http://localhost:3005` with backend at `:8000`. Playwright fixtures seed via Django management command (`python manage.py loaddata e2e_seed.json` or a custom command that creates school + BCBA + teacher + student).
- Write E2E tests for the five highest-risk BI user journeys:
  1. Therapist creates a session, records one observation of each data-collection type, writes a note, captures three signatures, closes the session. Assert all views reflect closed-immutability.
  2. Admin creates a choice set, adds options, reorders them; therapist sees new options in `SessionNoteForm`.
  3. BCBA creates a program (goal + targets + task-analysis steps), then therapist records task-analysis observations against it.
  4. BCBA generates a report from template, edits it in TipTap, downloads the PDF. Validate the PDF has non-zero byte count and contains expected student identifier text.
  5. School without `ai_narrative_enabled` — AI button is disabled; school with it enabled — button generates and writes narrative.
- Each E2E test also runs against the **career-readiness** critical path to catch cross-feature regressions (a student dashboard load, course assignment creation, single lesson submission).

## Phase 5 — Bug triage loop

Only after Phases 0–4 land does this phase begin. Phases above exist precisely to make this loop productive.

- Run the full suite (backend pytest incl. `integration`, frontend vitest, Playwright E2E) and triage every failure into `docs/BUGS.md` with severity (blocker / major / minor / cosmetic) and surface (BE / FE / contract / infra).
- Fix blocker + major only. Each fix: (a) write a failing test at the narrowest layer that catches it, (b) fix, (c) confirm no other tests regress, (d) commit with a descriptive message referencing the bug id.
- Do manual human QA on the five E2E flows above in a browser while watching the backend log for silent 500s / warnings. Anything weird gets a bug entry. Pay special attention to the surfaces that can't be easily auto-tested: TipTap editor UX, signature canvas ergonomics on touch devices, PDF rendering fidelity, AI narrative readability.
- Re-run full suite after all blockers/majors are resolved. Document remaining minor/cosmetic issues as follow-up tickets.

## Phase 6 — Production readiness

- **CI**: create `.github/workflows/ci.yml` at the repo root running three jobs on every PR: (1) backend `./scripts/pytest.sh -m "not e2e"` against Postgres service container, (2) frontend `yarn lint && yarn test && yarn build`, (3) Playwright E2E against `docker-compose.e2e.yml`. Delete or subsume the frontend-only workflow. Upload coverage to an artifact.
- **Secrets audit**: `SECRET_KEY`, `SIGNATURE_ENCRYPTION_KEY`, `FIELD_ENCRYPTION_KEY`, `OPENAI_API_KEY`, AWS creds — confirm every required var is documented in `backend/django_server/.env.dist`, listed in a deployment runbook, and not checked in. Add a Django management command `check_production_env` that refuses to start if any required prod var is unset.
- **Encryption-key rotation runbook**: `SIGNATURE_ENCRYPTION_KEY` overrides `FIELD_ENCRYPTION_KEY`; document exactly how to rotate without losing existing signatures (the usual pattern: read with old key, re-encrypt with new, commit atomically per row).
- **Logging & observability**: confirm structured logging on the LLM failure path (502s from `AISessionNarrativeService` are counted and rate-watched), on signature uploads, and on closed-session 403s. Add a `/healthz` and `/readyz` if not already present.
- **Rate limiting**: add DRF throttles to `POST /sessions/<id>/generate-narrative/` — the LLM is the only direct cost vector.
- **DB migrations dry-run**: run `python manage.py migrate --plan` against a copy of the production schema. Pytest runs `--nomigrations` so migration correctness isn't covered by the suite; add a single `@pytest.mark.integration` test that spins a fresh DB and runs every migration to completion.
- **Feature flag**: confirm `School.has_behavior_intervention_access` is OFF by default for all existing schools. BI rollout = flipping this flag per-school. Document the per-school flip procedure.
- **Postman**: the backend has a Postman collection convention ([backend/CLAUDE.md](backend/CLAUDE.md)). Ensure every new BI endpoint added during this plan is reflected there.

## Files to touch

**Create:**
- `.github/workflows/ci.yml` (root)
- `docker-compose.e2e.yml`
- `docs/DEV_SETUP.md`, `docs/BUGS.md`, `docs/runbooks/encryption-key-rotation.md`, `docs/runbooks/bi-school-rollout.md`
- `backend/django_server/openapi.yaml`
- `backend/django_server/behavior_intervention/reports/tests/test_{models,template_views,report_views,template_data_view,report_data_service}.py`
- `backend/django_server/behavior_intervention/tests/test_integration_{session_lifecycle,platform_access,ai_phi_exclusion,signature_encryption,choice_set_slugs}.py`
- `frontend/src/services/generated/api-types.ts` + `frontend/src/services/dto-contract.test-d.ts`
- Co-located `*.test.tsx` for components listed in Phase 3a
- `e2e/` directory with Playwright tests for the 5 BI journeys + 3 career-readiness sanity journeys

**Modify (small touches):**
- `backend/django_server/requirements.txt` — add `drf-spectacular`
- `backend/django_server/*/urls.py` — wire schema endpoint
- `frontend/package.json` — add `openapi-typescript`, `msw`, `@playwright/test`, `tsd`
- Existing DRF serializers / frontend DTOs for each contract mismatch found in Phase 1
- `frontend/.github/workflows/ci.yml` — delete (replaced by root CI)

## Verification

The plan is complete when **all** of the following are true:

- `./scripts/pytest.sh` passes in backend root (includes new `@pytest.mark.integration` tests).
- `./scripts/pytest-e2e.sh` passes (if any e2e-marked backend tests are added).
- `yarn test && yarn lint && yarn build` pass in `frontend/`.
- `docker compose -f docker-compose.e2e.yml up --build` brings the stack up and Playwright suite passes against it.
- `drf-spectacular` schema regen produces no diff against committed `openapi.yaml`.
- Backend line coverage for `behavior_intervention/` ≥ 80% (stretch: 90%); frontend coverage for `app/behavior-intervention/` ≥ 70%.
- Root CI is green on a clean PR with all three jobs.
- Manual QA on the five BI journeys in a browser produces no P1/P2 bugs.
- `docs/BUGS.md` has zero open blocker or major items.
- Every env var required in production is documented in `.env.dist` and `check_production_env` passes against a prod-shaped env.

## Out of scope

- Rewriting any BI code beyond what a specific bug fix requires. This is a stabilization plan, not a refactor.
- Migrating the hand-written DTOs to generated ones wholesale (Phase 1 uses a compatibility type-test instead — cheaper, same protection).
- Career-readiness work beyond sanity E2E coverage and contract verification.
- Replacing the git subtree workflow with submodules or a monorepo tool.
