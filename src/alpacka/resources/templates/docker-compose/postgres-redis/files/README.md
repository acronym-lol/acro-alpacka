# {{PROJECT_NAME}}

Postgres + Redis dev stack.

## Run

Click Launch in ALPACKA (runs `docker compose up`). To stop, hit the
red stop button on the project card — ALPACKA runs terminate on the
compose process and Docker tears containers down cleanly.

Manually:

```
docker compose up       # foreground
docker compose down     # stop + remove containers (volumes persist)
docker compose down -v  # also wipes the volumes (nukes data)
```

## Connection info

Defaults (override in ALPACKA's Edit Details → Variables):

- Postgres — `postgresql://postgres:postgres@localhost:5432/app`
- Redis    — `redis://localhost:6379`

Volumes `postgres_data` and `redis_data` persist across `docker compose
down`. Use `-v` to reset.

## Notes

- `POSTGRES_PASSWORD` is kept in `alpacka.yml` by default. If you'd
  rather not commit it, move it to `alpacka.local.yml` (which is
  git-ignored via `universal.gitignore`).
- The compose file uses `${VAR:-default}` interpolation, so it also
  works if you run `docker compose` outside ALPACKA — it just falls
  back to the defaults.
