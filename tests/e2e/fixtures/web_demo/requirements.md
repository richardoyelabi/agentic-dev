# Web Counter Demo

A minimal single-page web app used to exercise the runtime-driven UAT pipeline for `FrontendKind=web`. Kept deliberately tiny — UAT must be able to reach this through a real browser in under five minutes.

## Feature: Counter page

The app renders a single page with a number, an increment button, and a decrement button.

### Acceptance criteria

- **AC-001** — On first load, the page shows the number `0` in a heading-sized element.
- **AC-002** — Clicking the increment button raises the displayed number by one; the heading updates without a full page reload.
- **AC-003** — Clicking the decrement button lowers the displayed number by one; the value is allowed to go negative.

## Tech stack hint

Use a small modern stack (Vite + React or similar). Server-side rendering is not required. No backend is needed; this is `ProjectType=frontend_only`.
