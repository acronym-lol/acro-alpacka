"""CLI entry — run via `python main.py` during development, or install
with `pip install -e .` and invoke by name from PATH."""
from __future__ import annotations

import typer

app = typer.Typer(help="Your CLI description here.")


@app.command()
def hello(name: str = "world") -> None:
    """Say hello to NAME."""
    typer.echo(f"hello, {name}")


@app.command()
def version() -> None:
    """Print the CLI version."""
    typer.echo("0.0.1")


if __name__ == "__main__":
    app()
