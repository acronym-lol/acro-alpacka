# {{PROJECT_NAME}}

Express REST API starter.

## Setup

```
npm install
```

Optional: `cp .env.example .env` and tweak.

## Run

```
npm run dev      # hot-reloads on file change
npm start        # one-shot run
```

Routes live in `routes/`. Add a new file, mount it in `app.js`. Defaults
listen on `http://localhost:3000` — adjust via `PORT` / `HOST` (alpacka.yml
variables feed into the launch env automatically).

- `GET /` — service health
- `GET /hello` — example
- `GET /hello/:name` — example with a parameter
