# {{PROJECT_NAME}}

Flask HTTP service.

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
python app.py
```

Defaults to `http://127.0.0.1:5000`. ALPACKA's globe button on the
project card opens that URL once PORT/HOST are set.

## Endpoints

- `GET /` — service health
- `GET /hello/:name` — example
- `POST /webhook` — echoes the request body + headers (drop-in webhook receiver)

## Production

Flask's built-in server is dev-only. For production wire up gunicorn or
uwsgi:

```
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```
