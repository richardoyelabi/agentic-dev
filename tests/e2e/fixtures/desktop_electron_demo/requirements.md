# Electron Hello Demo

A minimal Electron desktop app used to exercise the runtime-driven UAT pipeline for `FrontendKind=desktop` with `desktop_framework=electron`. UAT drives this through Playwright attached over CDP.

## Feature: Hello window

A single main window that displays a heading and a button.

### Acceptance criteria

- **AC-001** — Launching the packaged or `electron .` app opens exactly one window whose heading reads `Hello, Electron!`.
- **AC-002** — The window contains a button labeled `Greet`. Clicking it replaces the heading with `Hello again, Electron!`.
- **AC-003** — Closing the only window exits the app (process leaves no zombie on macOS, Linux, or Windows).

## Tech stack hint

Electron 28+ with a minimal HTML/JS renderer. No bundler required. The architect must emit `desktop_framework: electron` in the frontend spec so the dispatcher picks `uat_desktop_electron`. No backend is needed; this is `ProjectType=frontend_only`.
