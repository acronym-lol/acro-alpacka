# Launch Dependencies

A **launch dependency** is a companion command ALPACKA spawns alongside a project's main launch — a Redis sidecar for a TD scene, a Python OSC bridge, a `npm run dev` for a coupled web UI, a one-off migration that runs once before the app is ready. Dependencies are kind-agnostic: any project, regardless of `kind`, can declare them.

## Why

You have scripts you'd otherwise spawn by hand every time you open the project. ALPACKA tracks launches anyway — it might as well bring those scripts up with the project, take them down on Stop, and surface their status next to everything else.

## Add one

In Edit Details (kebab menu on a project card → **Edit Details**), scroll to the **Launch Dependencies** section. Click **Add dependency** and fill in:

- **Name** *(optional)* — a short label that shows up in the inline-output prefix and Edit Details status pill. Falls back to the dep's index when blank.
- **Command** — the shell-style command line. Parsed with `shlex.split(posix=True)`, so quoting works the way it does in bash. Supports `{VAR}` / `${VAR}` substitution from the project's [variables](configuration.md#alpackayml-schema) — for example, a project variable `PORT=9000` lets you write `python worker.py --port {PORT}`.
- **Enabled** *(default on)* — uncheck to leave the dep configured but skip launching it. Useful when you're temporarily debugging without a sidecar but don't want to lose the row.
- **Run once** *(default off)* — declares the process as a one-shot. ALPACKA records its exit code when it terminates and **doesn't** auto-restart it as part of crash relaunch. Persistent (default) deps stay running until explicitly stopped.
- **Keep alive** *(default off)* — declares that this dep should survive when the main project process dies. Default behavior: the crash monitor terminates deps when main exits, so the project goes back to "all stopped" cleanly. Flip this on for a logger / recorder you'd rather keep collecting until you Stop the project explicitly. The card's Stop button always terminates everything regardless.

Save. The next launch brings the dep up with the project.

## What you'll see while running

While the project is running, each dep row in Edit Details shows a live status pill (refreshed on a 4-second timer):

| Pill | Meaning | Color |
|---|---|---|
| `● running 12345` | The process is alive (PID shown). | MATRIX GREEN |
| `✓ done` | A one-shot exited cleanly (exit 0). The row stays visible until the next launch wipes it. | FACTORY GREEN |
| `✗ exit N` | A one-shot exited with a non-zero code. | BLAZE |
| `✗ stopped` | A persistent dep died unexpectedly (briefly visible until the next 6-second sweep removes the row). | BLAZE |
| `– not spawned` | Project is running but ALPACKA didn't start this dep — usually because the command failed to spawn (e.g. binary not on PATH). Check stderr. | STEEL BLUE |

The card itself shows `● RUNNING (N)` where `N` counts every tracked process for the project — the main one plus each running dep.

## Inline output

If the project's `output_mode` is `inline`, ALPACKA captures every dep's stdout into the project's shared output buffer. Each line gets prefixed with the dep's name in brackets so streams stay distinguishable:

```
TouchDesigner started, opening MyScene.toe
[redis] Server started on 6380
[osc-bridge] Listening on 0.0.0.0:7000
TouchDesigner: scene loaded
[redis] received auth from 127.0.0.1
```

Open the captured-output dialog with the **logs** icon on the card's tool row (visible only for non-TD projects in inline mode). The window stays open across library refreshes and rolls a 5000-line ring per project.

If the project is in `console` mode, **each** dep gets its own terminal window — a deliberate consequence of "inherit the project's output mode." If you don't want N terminal windows, switch the project to inline or silent.

## Lifecycle rules

| When… | Persistent dep (default) | Persistent dep with Keep alive | Run-once dep |
|---|---|---|---|
| **Stop** clicked on the card | Killed | Killed | Killed (or already exited) |
| **Main exits cleanly / crashes** (including a run-once main run finishing) | Killed by crash monitor | Stays running | Already exited or running its own course |
| **Crash relaunch fires** (`launch_on_startup` + `reopen_on_crash`) | Killed before relaunch, respawned with the new main | Stays running through the relaunch | Re-spawned fresh with the new main |
| **Dep crashes by itself** | Row removed on next sweep; status pill flips to `✗ stopped` until the row clears | Same | One-shot's row stays with exit code; persistent's row goes away |
| **ALPACKA itself quits** (inline-mode deps) | SIGTERM via process group, then SIGKILL after 3s grace | SIGTERM via process group, then SIGKILL after 3s grace | Same |
| **ALPACKA itself quits** (silent / console deps) | Survive (independent of ALPACKA's session) | Survive | Survive |

## Examples

### Redis sidecar for a TD scene

Project variables:
```
REDIS_PORT=6380
```

Launch dependency:
```
Name:    redis
Command: redis-server --port {REDIS_PORT}
Enabled: ✓
```

The TD project's DAT scripts read `REDIS_PORT` from `os.environ` (or substitute it into a Run command for non-TD kinds). Stop the project → Redis dies. Start over → fresh server.

### Pre-launch migration

Run-once helper that has to succeed before TD opens the scene. (For now, treat it as a regular oneshot dep — failures don't block the main launch in v1; you'll see `✗ exit N` if it fails.)

```
Name:    migrate
Command: python scripts/migrate.py
Run once: ✓
```

### Logger that outlives crashes

A long-running stats collector that you'd rather keep alive across TD crashes (so you don't lose its buffered state):

```
Name:       stats-collector
Command:    python tools/collect.py --output logs/stats.jsonl
Keep alive: ✓
```

### NodeJS dev server alongside a Docker-Compose backend

If your `kind` is `docker-compose` and the main launch runs `docker compose up`, you can add the front-end dev server as a dep:

```
Name:    web-dev
Command: pnpm --dir frontend run dev
Enabled: ✓
```

Set the project's `output_mode` to `inline` to see Compose logs and `[web-dev]` lines interleaved in the same output window.

## Troubleshooting

- **"– not spawned" pill** — the dep's command failed to start. Common causes: binary not on PATH, syntax error in the command (unbalanced quotes are caught at save time, but malformed paths slip through), missing variable substitution. Check stderr for `[alpacka] Failed to start launch dependency '<name>': …`.
- **Dep doesn't die on Stop** — `terminate_process` sends SIGTERM (or `WM_CLOSE` on Windows) and waits 5 seconds before escalating to SIGKILL. If your script catches SIGTERM and refuses to exit, the kill will eventually win.
- **Dep restarts unexpectedly after a TD crash** — that's by design when `reopen_on_crash` is on for the project. Disable autostart relaunch (gear icon → Autostart Settings → Reopen on crash off) if you don't want the deps to come back along with TD.
- **Dep's output isn't visible** — check the project's `output_mode`. `none` (silent) discards stdio; `inline` captures it; `console` opens a terminal. The output viewer button on the card is only shown for non-TD projects in inline mode.
- **Indices look wrong in the status pills** — pills correlate by index (`run_config_name="dep:<i>"`). If you reordered or inserted rows mid-session, the live state may show stale-ish until the next launch. Save and relaunch to re-index.

## Reference

For the on-disk schema, the ownership rules, and the precise lifecycle semantics, see [`configuration.md` § `launch_dependencies`](configuration.md#launch_dependencies). For the underlying process tracking — kind-dispatched liveness, the crash monitor, the `terminate_project_processes` helper — see [Process Tracking](process-tracking.md).
