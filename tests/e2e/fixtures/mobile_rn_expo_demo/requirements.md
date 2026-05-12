# React Native (Expo) Counter Demo

A minimal React Native Expo app used to exercise the runtime-driven UAT pipeline for `FrontendKind=mobile` with `mobile_framework=react_native_expo`. UAT drives this through Maestro.

## Feature: Counter screen

A single screen with a number, an increment button, and a reset button.

### Acceptance criteria

- **AC-001** — Launching the app via `expo start` on a booted simulator/emulator shows a screen with the number `0` rendered in a large `Text` element.
- **AC-002** — Pressing a button labeled `+` raises the number by one without reloading the bundle.
- **AC-003** — Pressing a button labeled `Reset` returns the number to `0` regardless of its current value.

## Tech stack hint

Expo SDK 50+ with TypeScript template trimmed to the above. The architect must emit `mobile_framework: react_native_expo` in the frontend spec. Maestro is the primary driver; if absent, the integration test runner (`detox` or RN's built-in `jest`) is the fallback. No backend is needed; this is `ProjectType=frontend_only`.
