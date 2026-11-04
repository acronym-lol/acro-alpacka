# {{PROJECT_NAME}}

A Python CLI built with Typer.

## Setup

```
python -m venv .venv
.venv/Scripts/activate         # Windows
source .venv/bin/activate      # macOS / Linux
pip install -e .
```

## Run

```
python main.py hello            # direct invocation
python main.py hello --name Matt
python main.py version
```

Or after `pip install -e .`:

```
{{PROJECT_NAME}} hello
```

Add commands by decorating functions with `@app.command()` in `main.py`.
