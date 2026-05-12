"""Entry point for the cli-demo-adopt CLI."""

import typer

app = typer.Typer(help="Tiny CLI used as an adoption-test source.")


@app.command()
def greet(name: str = "world") -> None:
    """Print a greeting."""
    typer.echo(f"Hello, {name}!")


if __name__ == "__main__":
    app()
