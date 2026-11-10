# Development

## Run from source

```bash
cd acro-alpacka
python -m venv .venv
.venv/Scripts/activate               # Windows
pip install -e .
python -m alpacka
```

## Live-reload tips

- **Stylesheet** — edits to `src/alpacka/ui/style.qss` are picked up automatically via a `QFileSystemWatcher` installed at app startup. No restart needed.
- **SVG icons** — added icons are loaded on card build. Trigger a Library refresh (sort, search, or Refresh button) to see them.
- **Python code** — widget-layout / logic changes require a restart. `importlib.reload()` doesn't propagate to already-instantiated Qt widgets.

## Adding an icon

1. Drop a Lucide SVG into `src/alpacka/resources/icons/`.
2. Replace `stroke="currentColor"` with an explicit palette hex.
3. Reference in a QSS rule (`image: url({ICONS}/<name>.svg)`) OR load via `QIcon(str(_ICONS_DIR / "<name>.svg"))` from Python.

## Running tests

```bash
pip install -e ".[dev]"
pytest
```

Unit tests live in `tests/`. They cover the Qt-free modules (`project`, `approval`, `process_tracker`, `td_prefs`, `td_download`, `template_apply`, …) and run against temporary folders and a throwaway library DB — never your real config, projects or TouchDesigner's `pref.txt`. CI runs them on Windows and macOS for every push and pull request to `dev` and `main`.

## Running headless smoke tests

```bash
QT_QPA_PLATFORM=offscreen python -m alpacka
```

Useful for scripted tests that create / list projects without actually popping a visible window. On Windows the offscreen platform has no fonts unless you also set `QT_QPA_FONTDIR=C:/Windows/Fonts`, which matters if you grab screenshots.

## Packaging

```bash
pip install -r requirements.txt    # pulls PyInstaller
python build.py
# Output: dist/ALPACKA/
```

The spec uses `--onedir --windowed` so the build is a folder containing `ALPACKA.exe` (Windows) or `ALPACKA.app` (macOS). Zip and distribute.

On macOS, PyInstaller signs `ALPACKA.app` at the end of its build. Anything added to the bundle afterwards breaks that signature, and macOS then says a downloaded copy is damaged and won't open it. So `build.py` signs the app again (ad hoc) after copying in the licence files, then checks it with `codesign --verify --deep --strict` and fails if the check doesn't pass. `installer/make_dmg.sh` packages the DMG and checks the app inside it again. CI runs both on every pull request.

### Windows installer and in-app updates

`installer/alpacka.iss` builds the Inno Setup installer. **Update and restart** downloads the release's setup, checks it against `SHA256SUMS-windows.txt` and runs it with the command line `updater.installer_command` builds:

```
ALPACKA-<v>-windows-x64-setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /LOG=<file> /waitpid=<pid> /relaunch=1
```

- `/waitpid=` makes the setup wait (up to 30 s) for that ALPACKA to quit before it replaces any file.
- `/relaunch=1` makes a silent install open ALPACKA again. A silent install without it doesn't.

Only the installed build offers this. The running exe must be in the folder the installer recorded under `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\{928D3EA7-C282-4066-995E-A6B0CC034E98}}_is1`. That key really ends in `}}`, because Inno has no escape for `}`. Never "fix" the `AppId`; a test checks the key and the `.iss` agree.

The CI job **Build Windows installer** builds two installers from one bundle and runs `installer/test_update.ps1`. It installs A, starts it, runs B with that command line, and checks that B waits, installs and opens ALPACKA again. The script installs ALPACKA for the current user, so only run it on a spare machine.

## Code conventions

- Relative imports inside the `alpacka` package (`from . import db`, `from .ui.main_window import MainWindow`). Never `from alpacka import X`.
- Private helpers prefixed with `_`.
- Dataclasses for state objects (`Project`, `LibraryEntry`, `ProcessRecord`, `TDInstall`, `TemplateConfig`, …).
- Platform-specific code gated on `sys.platform`, not dynamic `os.name` checks.
- Qt widgets set object names for QSS selectors rather than inline styling.
- Dynamic properties with matching `[property="value"]` QSS selectors for state-dependent styling, not separate object names. In use: `pinned` (project card), `running` and `needsReview` (`#launchButton`).
- **Dialogs** inherit `ui.frameless.FramelessDialog` and add widgets to `self.content_layout`. For info / warning / question prompts use `frameless.info / warning / critical / question / choose` — never `QMessageBox`, which breaks the frameless chrome and the theme.
- **Buttons and labels:** an app-level event filter (`app._PointerCursorFilter`) gives every `QPushButton` a pointer cursor and every `QLabel` plain-text rendering. Don't set cursors by hand, and don't use rich text in labels.
- **Icons:** Lucide SVGs ship with `stroke="currentColor"`, which `QIcon` ignores — replace it with an explicit palette hex. Line icons use stroke width 1.75; filled icons use their role's accent colour. In QSS use `url({ICONS}/<name>.svg)` (the placeholder is substituted at load time). Add a source / licence comment to any third-party SVG and list it in `THIRD_PARTY_NOTICES.md`.
- **Colours** come from the Acronym palette; `style.qss` comments say which slot each colour serves. Prefer an existing slot to a new colour.
- **`clicked` carries a bool.** Slots connected to `QPushButton.clicked` receive the checked state, so a lambda with a keyword default must absorb it: `lambda _checked=False, url=repo_url: signal.emit(row_id, url)`.
- **`nativeEvent` returns a tuple.** `MainWindow.nativeEvent` and `FramelessDialog.nativeEvent` return `(False, 0)` for unhandled events; a bare `False` triggers Shiboken conversion errors.

## Safety rules

ALPACKA runs other people's projects and templates, so a few rules keep their files from running things on a machine without the user's say-so:

- **Nothing from a project's `alpacka.yml` runs until it's approved** (`approval.py`). The check is in `launch._spawn`, which every launch path goes through; don't add a launch path that bypasses it. See [Reviewing what a project runs](library.md#reviewing-what-a-project-runs).
- **Never build a shell / `cmd.exe` command string from project, template or settings data.** Pass argument lists, set the folder with `cwd=`, and on Windows quote with `launch_runner._quote_windows_arg` if a string is unavoidable.
- **`alpacka.local.yml` values never reach `alpacka.yml`.** `project.save()` writes only the fields the caller changed; don't bypass it with a raw dump of a loaded Project.
- **Template paths stay inside their folders** (`template_apply._inside`): no absolute paths, `..` or symlinks.
- **Write files with `fsutil.write_text_atomic`**, never via a fixed `*.tmp` name next to the target.
- **Only kill a process ALPACKA started.** Liveness, focus and terminate go through `kinds.base.match_process`, which checks the process start time (or, for old rows, the name). A PID with no record is never killed.
- **Downloaded installers must be verified** (`td_download.verify_installer`) before they run.

## Platform gotchas

- **Windows: psutil is slow on a cold start.** `psutil.process_iter(["name"])` can take seconds the first time, because for each process it can't open, `name()` enumerates every process again. `process_tracker.any_td_running()` uses one Toolhelp32 snapshot instead (~30 ms). Don't scan all processes with psutil on the UI thread.
- **macOS: don't launch TD with `open -a`** — see [Process Tracking](process-tracking.md#pid-capture).
- **Windows focus: only `SW_RESTORE` a minimized window** — see [Process Tracking](process-tracking.md#focus).

## TouchDesigner `pref.txt`

`td_prefs.py` manages a few keys in TD's machine-wide `pref.txt`, which every TD build and project on the machine shares ([user docs](td-preferences.md)). When changing it:

- **Only `td_prefs.reconcile()` writes the file.** It runs from `launch._spawn()` whenever an install is passed (every TD launch path ends up there) and from Settings Apply / Revert.
- **Merge only the keys the user accepted.** Every other line stays verbatim and in order; a one-time `pref.txt.alpacka-backup` is taken before the first write; each key's pre-ALPACKA value is kept so it can be put back.
- **Never write while any TouchDesigner process is running** (`process_tracker.any_td_running()`), including ones ALPACKA didn't start: a running TD rewrites the whole file from memory when its Preferences dialog closes. Reading is fine.
- **Never block a launch.** `reconcile` swallows errors into an `error` result.
- **No silent changes.** The user opts in per key; launch-time writes show in the main window's notice banner.
- **Never write `WATCH_ONLY` keys** (`general.pythonpackages64` and the other Python-path keys).
- **State lives in `td_prefs.json`, not `settings.json`**: `MainWindow` saves its `Settings` object whole and would overwrite anything written there during a launch.
- **`td_default` must be TD's real default.** TD drops a line once its value is back at the default, so a wrong `td_default` reads as a change made outside ALPACKA on every launch. Find a default by changing the setting in TD's dialog until its line disappears from `pref.txt`.

## Git workflow

- `main` is protected; every change lands through a pull request.
- `dev` is the integration branch. Branch from `dev` and open the PR back into `dev`: `feature/<slug>`, `fix/<slug>`, `chore/<slug>`, `refactor/<slug>`.
- Releases: a PR from `dev` into `main`. The version bump (`src/alpacka/__init__.py`) goes in before that merge; then tag the merge `v<version>` and push the tag, which builds and publishes the release.
- Commit messages: short, lowercase, imperative, one line, no trailing period (e.g. `add auto-launch + auto-relaunch on crash/freeze per project`).

## Architecture primer for new contributors

1. Start in `app.py` — the entry point. It initializes the DB, seeds templates, sweeps stale processes, installs event filters / file watchers, and boots `MainWindow`.
2. `ui/main_window.py` wires the views — Library, Templates, Settings, plus the in-stack `NewProjectView` and `EditDetailsView` pages — into a sidebar + content stack.
3. `ui/library_view.py` is the heart: it owns the card grid, the 6-second `QTimer` polling `process_tracker.sweep_dead()` + driving `crash_monitor.tick(...)`, and the launch / focus / stop / edit handlers. Card signals flow into its `_on_*` methods.
4. The launch pipeline is `ui/project_card.py → ui/library_view.py → launch.py → kinds/<kind>.py`. The kind handler builds a `LaunchSpec`; `launch._spawn` dispatches to `launch_runner.run_silent | run_inline | run_console`, registers the PID via `process_tracker.register(...)`, and finally calls `_spawn_launch_dependencies` to bring up companion processes. Most interesting bits: version-mismatch handling (`_resolve_version_mismatch_once`), batch spawn (`_launch_batch`), and dep spawning (`_spawn_one_launch_dependency`).
5. Template config lives in `template_apply.py`; scaffold subprocesses in `template_scaffold.py`; universal bootstrap in `universal_bootstrap.py`; all called from `create.py` after the template files are written.
6. `process_tracker.py` is the only place that imports `psutil` and does ctypes window work for the default focus / hang-detection paths. Kind-specific window logic (TD's AppleScript fallback, etc.) lives in the per-kind handler.

## Adding a kind

Each project kind ships as one file under `src/alpacka/kinds/`. Implement the `KindHandler` protocol from `kinds/base.py`:

1. Pick a unique `name` (e.g. `"rust"`) and `display_name`.
2. Implement `detect(folder) -> DetectResult` so Add Existing can score the folder against your kind. Higher confidence wins; return `confidence=0.0` for the kind to be ignored.
3. Implement `build_launch(project, run_config, merged_vars, **extras) -> LaunchSpec` returning argv / cwd / env / `process_name_pattern` / `run_config_name`. Use `kinds.base.build_argv_from_command` for the standard `command + args + {VAR}` substitution flow.
4. Implement `default_run_config(project)` and the lifecycle methods — most kinds delegate to `default_liveness` / `default_terminate` / `default_focus` from `kinds/base.py` and only override what's special. Keep the process identity check (`match_process`): if your kind has a name convention, set `default_process_name_pattern`; don't replace the check with a bare PID test.
5. If your kind has dependency installs (pip / npm / cargo build), implement `install_steps(project_folder)` and set `produces_install_steps = True` so the New Project dialog gates the post-create install on the user's checkbox.
6. Pick a `default_output_mode`: `"console"` for CLI tools, `"inline"` for long-running servers, `"none"` for kinds that own their own window (TD, executables).
7. Add an SVG icon to `src/alpacka/resources/icons/` matching `<icon-name>.svg`, then set `icon = "<icon-name>"` so `placeholder.py` can render it as the card-thumbnail fallback.
8. Register the handler in `kinds/__init__.py`'s import list.

The internal `dependency` kind is a special case — see `kinds/dependency.py` for a minimal stub handler whose only real job is routing `liveness/focus/terminate` for companion processes spawned by `launch._spawn_launch_dependencies`. It's never user-selected.
