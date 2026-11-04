# {{PROJECT_NAME}}

FastAPI service. ALPACKA's launch runs `python main.py` with `PORT`,
`HOST`, and `RELOAD` injected as env vars from `alpacka.yml`.

## Setup

```
python -m venv .venv
.venv/Scripts/activate         # Windows
source .venv/bin/activate      # macOS / Linux
pip install -e .
```

## Run

Click Launch in ALPACKA, or manually:

```
python main.py
```

Once it's up, open [http://localhost:8000](http://localhost:8000) (or the
port you configured) — ALPACKA's globe button on the project card jumps
there automatically.

Interactive docs: `/docs` (Swagger UI) and `/redoc`.

- `GET /` — service health
- `GET /hello/:name` — example
