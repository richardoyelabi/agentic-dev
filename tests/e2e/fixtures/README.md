# E2E Fixtures

Minimal fixture projects used by the **manual** end-to-end validation pass for the multi-frontend runtime-UAT feature. Each directory holds a tiny `requirements.md` (one feature, three acceptance criteria) — enough that running `agentic-dev new` against it reaches UAT quickly so the validator and per-kind UAT drivers can be exercised.

These fixtures are not consumed by pytest. They are inputs to the procedure in [`MANUAL_RUNBOOK.md`](../MANUAL_RUNBOOK.md).

| Fixture | `--frontend-kind` | UAT agent picked | Driver |
|---|---|---|---|
| [`web_demo/`](web_demo/) | `web` | `uat_web` | Playwright MCP |
| [`cli_demo/`](cli_demo/) | `cli` | `uat_cli` | subprocess via `Bash` |
| [`desktop_electron_demo/`](desktop_electron_demo/) | `desktop` | `uat_desktop_electron` | Playwright over CDP |
| [`desktop_tauri_demo/`](desktop_tauri_demo/) | `desktop` | `uat_desktop_tauri` | `tauri-driver` |
| [`mobile_flutter_demo/`](mobile_flutter_demo/) | `mobile` | `uat_mobile` | Maestro / Flutter integration_test |
| [`mobile_rn_expo_demo/`](mobile_rn_expo_demo/) | `mobile` | `uat_mobile` | Maestro |
| [`cli_demo_adopt/`](cli_demo_adopt/) | (detected) | n/a — adoption only | n/a |

`cli_demo_adopt/` is a pre-existing Python Typer CLI used as the source for the adoption-flow scenario (`agentic-dev adopt`); it has no `requirements.md` because there is nothing for the pipeline to build.

## When to regenerate

Whenever the pipeline's input-processor, structure-detector, or per-kind UAT prompts change in a way that affects what makes a "minimal acceptable demo." Otherwise the same fixtures can be re-used across runs.
