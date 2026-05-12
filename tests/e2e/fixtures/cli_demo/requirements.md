# CLI Greet Demo

A minimal command-line tool used to exercise the runtime-driven UAT pipeline for `FrontendKind=cli`. UAT drives this through a subprocess; the app must be fully non-interactive and exit cleanly.

## Feature: `greet` subcommand

The app exposes a single subcommand that prints a greeting to a named recipient.

### Acceptance criteria

- **AC-001** — `greet --name Ada` prints `Hello, Ada!` followed by a newline to stdout and exits with status code `0`.
- **AC-002** — `greet` with no `--name` flag prints `Hello, world!` and exits with status code `0`.
- **AC-003** — `greet --help` prints usage text mentioning `--name`, exits `0`, and writes nothing to stderr.

## Tech stack hint

Python + Typer or Click. The package should be installable so `greet` ends up on `PATH` after `pip install -e .`. No backend is needed; this is `ProjectType=frontend_only` with `FrontendKind=cli`.
