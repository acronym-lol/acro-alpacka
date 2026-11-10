# Architecture

## Stack

- **Python 3.11+**
- **PySide6 / Qt 6** — native desktop UI (frameless window with a custom chrome, theme via QSS)
- **SQLite** via stdlib — project / template / process registry
- **psutil** — process liveness + window focus / termination
- **PyYAML** — `template.yml` parsing

Packaged as a portable `ALPACKA/` folder via PyInstaller.

## Source layout

```
acro-alpacka/
├── src/alpacka/
│   ├── __main__.py              # CLI entry (`python -m alpacka` / `alpacka`)
│   ├── app.py                   # QApplication bootstrap, stylesheet hot-reload, auto-launch-on-startup
│   ├── paths.py                 # per-user + portable-mode config paths
│   ├── settings.py              # settings.json load / save
│   ├── db.py                    # SQLite schema + connection + migrations
│   ├── project.py               # Project dataclass + alpacka.yml I/O (round-trip via ruamel)
│   ├── library.py               # SQLite project registry, LibraryEntry dataclass
│   ├── tags.py                  # tag tree (tags.json) + drag/drop reparenting
│   ├── templates.py             # built-in template seeding + user templates CRUD
│   ├── template_apply.py        # template.yml loader + action runner
│   ├── template_scaffold.py     # post-create scaffold subprocess runner
│   ├── universal_bootstrap.py   # minimal folder/.gitignore setup for every new project
│   ├── create.py                # new-project creation pipeline
│   ├── add_existing.py          # import flow for existing folders
│   ├── launch.py                # kind-dispatched launch + dependency spawning + version-mismatch
│   ├── launch_runner.py         # subprocess runners per output_mode (silent/console/inline)
│   ├── output_manager.py        # per-project ring buffer + multi-PID capture workers
│   ├── crash_monitor.py         # auto-relaunch on crash/freeze + dependency teardown
│   ├── td_install.py            # detect installed TD versions per platform
│   ├── td_prefs.py              # managed keys in TD's machine-wide pref.txt (merge, drift, revert)
│   ├── td_download.py           # download + run the Derivative installer
│   ├── downloads.py             # HTTPS download with progress, cancel and hashing
│   ├── toe_inspect.py           # extract version from a .toe via `toeexpand -b`
│   ├── process_tracker.py       # PID persistence, kind-dispatched liveness/focus/terminate
│   ├── external.py              # open folder / VS Code / terminal / browser helpers
│   ├── updater.py               # check for a newer release; download + start the Windows setup
│   ├── kinds/                   # one handler per project kind
│   │   ├── base.py              # KindHandler protocol + shared default_liveness/_terminate/_focus
│   │   ├── touchdesigner.py     # TD: TOUCH_* env, AppleScript focus, name="touchdesigner"
│   │   ├── python.py            # Python: venv-aware launch, install_steps for pip
│   │   ├── nodejs.py            # NodeJS: npm-script detection, install_steps for npm/pnpm
│   │   ├── docker.py            # docker-compose: up/down + log streaming
│   │   ├── shell.py             # arbitrary shell command launches
│   │   ├── executable.py        # bare-binary kind for .exe / .app launches
│   │   ├── generic.py           # fallback for projects matching no specific signal
│   │   └── dependency.py        # internal kind for companion processes (launch_dependencies)
│   ├── ui/                      # every widget + dialog
│   │   ├── main_window.py       # frameless chrome + sidebar + content stack
│   │   ├── title_bar.py         # custom drag/min/max/close header
│   │   ├── frameless.py         # FramelessDialog + info/warning/critical/question helpers
│   │   ├── sidebar_tree.py      # collapsible tag/library/templates/settings sidebar
│   │   ├── library_view.py      # card grid + 6s process poll + launch/focus/stop handlers
│   │   ├── project_card.py      # single project card (state, actions, kebab menu)
│   │   ├── flow_layout.py       # custom QLayout for tag-tile flow row
│   │   ├── new_project_view.py  # full-page New Project (template picker, run setup)
│   │   ├── add_existing_dialog.py
│   │   ├── edit_details_view.py # full-page editor: variables, run cmd, deps, TD launch
│   │   ├── templates_view.py    # template registry CRUD
│   │   ├── settings_view.py     # detected TD installs, defaults, theme
│   │   ├── output_view.py       # modeless dialog reading per-project ring buffer
│   │   ├── autostart_settings_dialog.py
│   │   ├── td_install_dialog.py # version-mismatch + install-progress dialogs
│   │   ├── update_dialog.py     # Update and restart: download progress, then quit for the setup
│   │   ├── toe_picker_dialog.py
│   │   ├── tag_picker_dialog.py
│   │   ├── tag_edit_dialog.py
│   │   ├── tag_icon.py          # tinted-by-tag SVG renderer
│   │   ├── scaffold_progress_dialog.py
│   │   ├── dnd.py               # drag/drop MIME helpers
│   │   ├── placeholder.py       # hash-seeded gradient + kind-icon fallback thumbnail
│   │   └── style.qss            # Acronym dark theme
│   └── resources/
│       ├── icons/               # Lucide + brand SVGs
│       └── universal.gitignore  # baseline dropped into every new project
```

## Module layering

What can import what:

```
ui/*               → may import anything below
launch.py          → kinds, library, output_manager, process_tracker, project, td_install, td_prefs
crash_monitor.py   → launch, library, process_tracker
library.py         → db, process_tracker, project
create.py          → library, project, template_apply, templates, universal_bootstrap
process_tracker.py → db, kinds, psutil
kinds/*            → base only (one handler per file; no cross-kind imports)
template_apply.py  → PyYAML + fs (stands alone)
td_prefs.py        → paths + stdlib (stands alone, Qt-free)
db.py / paths.py / settings.py → stdlib + minor PySide
```

`app.py` is the root orchestrator. It runs DB init → template seeding → process sweep → QApplication setup → event filters / file watchers → MainWindow → auto-launch flagged projects.

## Key data

### On disk (per project)

- `alpacka.yml` — single source of truth for a project's metadata, round-tripped via `ruamel.yaml`. Launcher-owned top-level fields (`name`, `description`, `template`, `kind`, `tags`, `launch_on_startup`, `output_mode`, …) and kind-owned sections under `kind_data.<kind>` (AcroCore writes `kind_data.touchdesigner.{toe_file, td_version, td_major}` on every TD save) merge-save without clobbering each other. Lives inside the project folder so it travels with the project.
- `alpacka.local.yml` *(optional)* — sidecar for per-machine overrides. Read-only from the launcher.
- `.launcher-thumb.png` *(optional)* — card thumbnail. ALPACKA auto-picks it up if present; users can drop one in manually or set one via Edit Details.

### In the ALPACKA config dir

- `projects.db` — SQLite database. Tables: `projects` (library registry), `templates` (built-in + user-added), `running_processes` (tracked PIDs).
- `settings.json` — detected TD installs, default TD version, theme.
- `cache/installers/` — downloaded Derivative installers.

## Launch pipeline

```
project_card._emit_launch
  → library_view._on_launch(row_id, targets)        # targets = .toe filenames OR run-config names
    → kind branch:
        TD:    resolve install (maybe show version-mismatch) → _launch_batch loops → launch.launch_toe(entry, toe, install)
        Other: _launch_run_configs loops → launch.launch(entry, run_config_name)
      → launch._spawn(entry, …)
        → handler.build_launch(project, run_config, merged_vars, **extras)  # builds LaunchSpec(argv/cwd/env/...)
        → _dispatch_runner(spec, env, output_mode)  # silent / inline / console
          → launch_runner.run_silent | run_inline | run_console
        → process_tracker.register(row_id, pid, kind=…, process_name_pattern=…, run_config_name=…, output_mode=…)
        → if inline: output_manager.attach(row_id, proc)
        → if not long_running: spawn _watch_completion thread to record exit_code
        → library.mark_launched + project.touch_last_opened
        → _spawn_launch_dependencies(entry, project, merged_vars, output_mode)
          → for each enabled dep: shlex-split → run_silent|run_inline|run_console → register kind="dependency"
          → inline deps: output_manager.attach(row_id, proc, source=<label>)  # per-line `[label] ` prefix
```

When the card button is clicked for a project with tracked processes, the flow short-circuits: `_emit_launch` routes to `focus_requested` instead, which calls `process_tracker.focus_window(pid)` to raise the existing window.

### Output mode

Non-TD projects pick how Launch surfaces stdout/stderr — see [`configuration.md` § `output_mode`](configuration.md#output_mode) for the schema and per-kind defaults. The split between `launch.py` (orchestration), `launch_runner.py` (per-platform subprocess wrapping), and `output_manager.py` (Qt-aware capture buffer + worker thread) keeps Qt deps out of the headless launch path. Inline-mode children are tied to ALPACKA's session via the `aboutToQuit` shutdown hook in `app.py` — closing the app SIGTERMs them with brief grace before SIGKILL.

In inline mode, `output_manager` re-keys its capture-worker registry by `(row_id, pid)` so multiple processes for the same project (a main launch + N launch dependencies) coexist. The buffer itself is one per `row_id` — every worker appends into the same ring, so the OutputView dialog shows main + deps interleaved chronologically. Dep workers prefix each line with `[<source>] ` so the streams are distinguishable; only the main capture (no source label) drives the buffer's exit-status signal.

### Launch dependencies

Per-project sidecar commands declared in `alpacka.yml` as `launch_dependencies:` — see the dedicated reference in [`configuration.md` § `launch_dependencies`](configuration.md#launch_dependencies). Spawned **after** the main process so a dep failure can't poison the main launch's bookkeeping. Each dep registers as `kind="dependency"` with `run_config_name="dep:<index>"` so Edit Details can correlate the row back to the yml entry. Lifecycle teardown (Stop, main-died) is handled by `process_tracker.terminate_project_processes` and `crash_monitor._terminate_dependents` respectively — see [process tracking](process-tracking.md#crash-monitor).

## Running state

A `QTimer` in the library view sweeps `running_processes` every 6 seconds — for each row, `process_tracker.is_alive(pid)` dispatches through the kind handler's `liveness(record)` (defaults to a `psutil.pid_exists` + `process_name_pattern`-substring check). Dead rows are pruned; if anything changed the library refreshes. The same tick feeds `dead`/`alive` lists to `crash_monitor.tick(...)`, which decides whether to relaunch crashed-but-flagged projects and tears down companion deps that didn't opt into surviving the main process.

## Window chrome

The main window is frameless (`Qt.FramelessWindowHint`). A custom `TitleBar` widget provides the draggable header + min/max/close controls. On Windows, `nativeEvent` returns `WM_NCHITTEST` codes so the edges still resize natively (Aero snap works). All dialogs use the same frameless pattern via the `FramelessDialog` base class.

## Theme

The QSS lives at `src/alpacka/ui/style.qss`. It references the Acronym brand palette and defines distinct shades for each depth tier (title bar, action bar, content, library well, sidebar). Icon paths are substituted in at load time via a `{ICONS}` placeholder. The app also installs a `QFileSystemWatcher` on the QSS file so saved edits live-reload without restart.
