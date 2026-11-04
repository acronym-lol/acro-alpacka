# {{PROJECT_NAME}}

Standalone Redis 7 dev environment.

## Run

Click Launch in ALPACKA (runs `docker compose up`). Stop via the red
button on the project card or `docker compose down`.

```
docker compose up -d         # background
docker compose logs -f       # follow logs
docker compose down          # stop (volume persists)
docker compose down -v       # also wipe the data volume
```

## Connect

```
redis://localhost:6379             # no auth
redis://:password@localhost:6379   # with REDIS_PASSWORD set
```

(Override port via Edit Details → Variables.)

Quick sanity:

```
docker compose exec redis redis-cli ping
docker compose exec redis redis-cli set hello world
docker compose exec redis redis-cli get hello
```

## Notes

- `REDIS_PASSWORD` defaults to empty (no auth) for laptop dev. If you
  set one, move it to `alpacka.local.yml` so it stays out of git.
- The `docker-compose/postgres-redis` template runs both services
  together if you need that combo — don't stack this one on top of
  it, they'll fight over the same port.
- Compose uses `${VAR:-default}` interpolation, so running outside
  ALPACKA still works — defaults kick in.
