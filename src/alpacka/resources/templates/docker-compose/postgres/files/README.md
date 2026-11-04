# {{PROJECT_NAME}}

Standalone Postgres 16 dev environment.

## Run

Click Launch in ALPACKA (runs `docker compose up`). Stop via the red
stop button on the project card or `docker compose down`.

```
docker compose up -d         # background
docker compose logs -f       # follow logs
docker compose down          # stop (volume persists)
docker compose down -v       # also wipe the data volume
```

## Connect

```
postgresql://postgres:postgres@localhost:5432/app
```

(Override via Edit Details → Variables.)

Quick sanity:

```
docker compose exec postgres psql -U postgres -d app -c '\dt'
```

## Notes

- `POSTGRES_PASSWORD` lives in `alpacka.yml` by default. Move it to
  `alpacka.local.yml` (git-ignored) for anything you don't want
  committed.
- Compose uses `${VAR:-default}` interpolation, so running outside
  ALPACKA still works — the defaults kick in.
