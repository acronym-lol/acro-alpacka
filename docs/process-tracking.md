# Process Tracking

ALPACKA tracks every process it spawns — TouchDesigner, Python entrypoints, dev servers, launch dependencies — so cards show running state, the launch button focuses an existing window instead of double-launching, Stop terminates cleanly, and the crash monitor can auto-relaunch. Tracking is **kind-dispatched**: each project's `kind` selects a `KindHandler` (in `src/alpacka/kinds/`) that owns liveness, focus, and terminate semantics for processes of that type.

## Records

The `running_processes` table holds one row per spawned process. A project can own multiple rows simultaneously: one for the main process plus one per [launch dependency](configuration.md#launch_dependencies) — those register with `kind="dependency"` and `run_config_name="dep:<index>"`.

`process_tracker.ProcessRecord` is the in-memory shape. Key fields:

| Field | Notes |
|---|---|
| `kind` | Selects the handler — TD's `"touchdesigner"`, `"python"`, `"nodejs"`, `"docker-compose"`, `"executable"`, `"shell"`, `"generic"`, or `"dependency"` for companion processes. |
| `process_name_pattern` | Lower-cased substring required in `psutil.Process(pid).name()` for the row to count as alive. Empty string ⇒ pid-only liveness. Defends against PID reuse (see below). |
| `run_config_name` | The named run config the launch came from. Deps use `"dep:<index>"` so Edit Details can correlate them back to the `launch_dependencies` entry. |
| `output_mode` | `"none"`, `"inline"`, or `"console"` — recorded so terminate / shutdown can branch on capture state without re-reading the project file. |
| `exit_code`, `completed_at` | Filled in by `mark_completed` when a one-shot watcher sees the process exit. Persistent processes have these blank until they die (then `sweep_dead` removes the row). |
| `keep_alive_after_main`, `oneshot` | v5 dependency flags. See [`launch_dependencies`](configuration.md#launch_dependencies) for the lifecycle rules they drive. |

## PID capture

- **Windows** — `subprocess.Popen([exe, …])` already yields the real child PID.
- **macOS (TD console-mode)** — TD specifically is invoked through `<TD.app>/Contents/MacOS/TouchDesigner` directly, not `open -a`, because `open -a` returns the PID of a short-lived shell helper rather than TD itself. Console-mode launches on macOS instead spawn a `.command` script via `open -a Terminal`, then poll a temp PID-file the script writes with `$$` — the resolved PID gets wrapped in `_AdoptedProcess` (see `launch_runner._run_console_macos`) so callers can use it like a regular `Popen`.
- **Linux (console)** — terminal emulators are tried in order (`x-terminal-emulator`, `gnome-terminal`, `konsole`, etc.); the wrapper PID belongs to the terminal, and its lifetime tracks the inner command.

After spawning, `launch._spawn` calls `process_tracker.register(...)` with the kind, name pattern, run-config name, and output mode. For inline-mode launches it then hands the `Popen` to `output_manager.attach(row_id, proc, source=...)` for stdio capture; for one-shot run configs it spawns a `_watch_completion` daemon thread that calls `mark_completed` when the process exits.

## Liveness — why an identity check matters

`process_tracker.is_alive(pid)` looks up the record, picks the kind handler, and dispatches:

```python
handler = kinds.get(rec.kind)
return handler.liveness(rec)
```

Every kind goes through `kinds.base.match_process(record)`, which answers "is the process at this PID still the one ALPACKA started?":

1. **Start time (schema v8 and later).** `register()` stores the process's OS start time (`psutil` `create_time`). If the process now at that PID started at a different time, it isn't ours.
2. **Name (older rows).** Rows registered before v8 have no start time, so the process name must contain `record.process_name_pattern`.

This defends against **PID reuse**. After a process quits, the OS is free to recycle its PID onto any unrelated new process. Without the check, ALPACKA would report the recycled PID as "still running" — and worse, Stop or the crash monitor could kill an unrelated program. We'd rather under-claim than over-claim.

Killing is stricter than reporting: before `terminate`, an older row with no name pattern either (a bare PID) is left alone, and a PID with no `running_processes` row is never killed. When adding a kind, keep the identity check: parameterize the name pattern, don't drop it.

Patterns are stored per-row so they reflect what was actually spawned: TD records `"touchdesigner"`, the Python kind records `"python"`, dependencies record whatever `psutil.Process(pid).name()` reports at spawn time (lower-cased, `.exe` stripped) so wrappers like `python -m worker` get the real exe name. An empty pattern disables the name check for older rows (pid-only liveness — used by the generic kind, where there's no convention to rely on); rows with a start time don't need it.

## Sweep

`process_tracker.sweep_dead()` walks every row, dispatches `handler.liveness(rec)` per kind, and removes rows whose process is gone. Two callers:

- A `QTimer` in the library view fires every 6 seconds; `sweep_dead()` returns the list of just-died records, which `crash_monitor.tick(dead, alive)` consumes for relaunch / dep-cleanup decisions. The card grid only refreshes when something actually disappeared, to avoid gratuitous UI rebuilds.
- `app.py` calls `sweep_dead()` once at startup before the auto-launch pass, so a project that was running at last shutdown doesn't get double-launched (or, more commonly, so a stale row from a session that was force-killed gets cleaned up before any UI sees it).

One-shot rows with `completed_at` set are **not** removed on sweep — they stay in place so the UI can surface the exit code (e.g. `✓ done` on a oneshot dep) for the rest of the session. They never count as running: cards don't show Stop or Focus for them, and Stop, the hang check and dependency clean-up skip them, because their PID may already belong to another program. `process_tracker.clear_completed()` drops them at the next startup.

## Focus

`process_tracker.focus_window(pid)` brings an existing window to the foreground without changing its size. Dispatches through `handler.focus(rec)`:

- **TouchDesigner** — Windows: `EnumWindows` finds top-level visible titled windows for the pid; calls `SetForegroundWindow(hwnd)`. If `IsIconic(hwnd)`, first calls `ShowWindow(SW_RESTORE)` — but **only** when actually minimized (calling `SW_RESTORE` on a normal window re-applies the saved maximize state, breaking "focus preserves size"). macOS: `open -a TouchDesigner` to activate via AppKit, with an AppleScript fallback. Linux: best-effort `wmctrl -ia <win_id>`.
- **Generic / nodejs / python / shell / docker-compose / executable** — the shared `kinds.base.default_focus` handles Windows + Linux the same way TD does. macOS has no generic activator (you'd need to know the .app bundle), so non-TD kinds no-op there.
- **Dependency** — no-op. Companion processes typically have no GUI; the card UI doesn't expose Focus on dep rows.

## Terminate

`process_tracker.terminate_process(pid, timeout=5.0)` dispatches `handler.terminate(rec, timeout)`. Most kinds use the shared `default_terminate`: it checks the process is still ours (`match_process`, see above), then graceful `proc.terminate()` (SIGTERM / WM_CLOSE), wait `timeout`, escalate to `proc.kill()` if still alive. The DB row is then removed.

`process_tracker.terminate_project_processes(project_id)` is the project-scoped variant: it iterates every row for the project, kills dependencies first (so a dep that talks to main doesn't spam connection-refused errors during the brief window where main is gone but the dep is still draining), then the main process. The Library card's Stop button uses this so "Stop" means "put the project back to idle," not "kill this one PID."

## Crash monitor

`crash_monitor.tick(dead, alive)` runs once per 6-second sweep cycle (called from `library_view._poll_running_processes`). It does two passes:

1. **Ended processes** — the dead records from the sweep, plus one-shot main runs whose watcher has just marked them completed (the sweep skips completed rows, so these would otherwise never reach the monitor; each finish is handled once). For each, decide whether to:
   - Skip (user-initiated stop, suppression flag was set by `_on_stop`).
   - Skip (the dead record is itself a `dependency` — a dep dying isn't a project crash).
   - Tear down companion deps via `_terminate_dependents(project_id)`. Persistent deps without `keep_alive_after_main` get `terminate_process`'d so they don't pile up across crashes; flagged deps stay running.
   - Optionally schedule a relaunch via `_schedule_relaunch(record)` if the project has `launch_on_startup AND reopen_on_crash` set.
2. **Hang detection** (Windows only, via `IsHungAppWindow`) — for each alive record, track unresponsive top-level windows; after `freeze_timeout_seconds` past the `startup_grace_seconds` window, force-terminate and treat the kill as a crash.

`_do_relaunch` re-enters the same pipeline a manual launch uses — `launch.launch_toe(entry, toe_filename)` for the TouchDesigner file that ended, `launch.launch(entry, run_config_name=…)` with the run config that ended for every other kind — so deps spawn fresh through `_spawn_launch_dependencies`. The race-guard at the top of `_do_relaunch` only checks for a still-running **main** process with the same target (`.toe` or run config): another run config of the same project still running, or surviving `keep_alive` deps, don't block a relaunch.

When Auto Relaunch is on, a console-mode launch doesn't wait for a key after an error: the console wrapper is the tracked PID, so a window waiting for a key would hide the crash until someone dismissed it.

## Auto-launch on startup

Per-project flag in `alpacka.yml` as `launch_on_startup: bool`, set via the per-card Autostart Settings dialog.

When ALPACKA boots:

1. `process_tracker.sweep_dead()` runs first, so we don't double-launch something that's still alive from a prior session.
2. `app._auto_launch_flagged()` iterates the library; for each project with the flag set AND no live tracked processes, it spawns a launch through `launch.launch(entry)` / `launch.launch_toe(...)`. Both go through `_spawn`, which spawns dependencies after the main process — so auto-launch covers companion processes for free.

Errors during auto-launch are non-fatal so a broken project doesn't block the launcher from opening.

## Schema

```sql
CREATE TABLE running_processes (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    pid INTEGER NOT NULL UNIQUE,
    toe_file TEXT NOT NULL DEFAULT '',
    td_version TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'touchdesigner',
    process_name_pattern TEXT NOT NULL DEFAULT 'touchdesigner',
    run_config_name TEXT NOT NULL DEFAULT 'default',
    output_mode TEXT NOT NULL DEFAULT 'none',
    exit_code INTEGER,
    completed_at TEXT,
    keep_alive_after_main INTEGER NOT NULL DEFAULT 0,
    oneshot INTEGER NOT NULL DEFAULT 0,
    create_time REAL          -- v8: OS process start time, for the identity check
);
```

Foreign-key cascade means removing a project from the library auto-deletes its tracked process rows (the actual processes leak — Library "Remove" never touches disk or processes, so the user is expected to Stop first if they want everything torn down). Schema bumps and migrations are tracked in [`configuration.md`](configuration.md#running_processes).
