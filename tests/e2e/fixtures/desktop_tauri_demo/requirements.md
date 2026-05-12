# Tauri Hello Demo

A minimal Tauri desktop app used to exercise the runtime-driven UAT pipeline for `FrontendKind=desktop` with `desktop_framework=tauri`. UAT drives this through `tauri-driver` (WebDriver), not Playwright.

## Feature: Hello window

A single main window that displays a heading and a button.

### Acceptance criteria

- **AC-001** — Launching `cargo tauri dev` (or the built binary) opens exactly one window whose heading reads `Hello, Tauri!`.
- **AC-002** — The window contains a button labeled `Greet`. Clicking it replaces the heading with `Hello again, Tauri!`.
- **AC-003** — Closing the only window exits the app cleanly.

## Tech stack hint

Tauri 1.5+ (Rust backend, plain HTML/JS frontend) — kept frameworkless on the JS side to stay small. The architect must emit `desktop_framework: tauri` in the frontend spec so the dispatcher picks `uat_desktop_tauri`. No backend is needed; this is `ProjectType=frontend_only`.
