# Flutter Counter Demo

A minimal Flutter mobile app used to exercise the runtime-driven UAT pipeline for `FrontendKind=mobile` with `mobile_framework=flutter`. UAT drives this through Maestro (primary) or Flutter's integration_test runner (fallback).

## Feature: Counter screen

A single screen with a number, an increment button, and a reset button.

### Acceptance criteria

- **AC-001** — Launching the app on a booted simulator/emulator shows a screen with the number `0` rendered in a large text widget.
- **AC-002** — Tapping the FloatingActionButton (or the labeled `+` button) raises the number by one. The change is visible without a restart.
- **AC-003** — Tapping a button labeled `Reset` returns the number to `0` regardless of its current value.

## Tech stack hint

Flutter 3.16+ with the default Material counter scaffold trimmed to the above. The architect must emit `mobile_framework: flutter` in the frontend spec. UAT prereqs require a booted non-web device — keep the spec compatible with both iOS and Android targets. No backend is needed; this is `ProjectType=frontend_only`.
