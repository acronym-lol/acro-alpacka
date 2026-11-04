# Configuration

## Paths

### ALPACKA config root

| OS | Default | Portable mode |
|---|---|---|
| Windows | `%APPDATA%/ALPACKA/` | `./data/` next to the exe (when `portable.txt` is present) |
| macOS | `~/Library/Application Support/ALPACKA/` | `./data/` |
| Linux | `~/.config/alpacka/` | `./data/` |

Contents:

- `projects.db` — SQLite registry (projects, templates, running_processes)
- `settings.json` — detected TD installs, theme, default install
- `td_prefs.json` — which TouchDesigner preferences ALPACKA manages, plus the values it found before changing them (see [TouchDesigner Preferences](td-preferences.md) and [§ `td_prefs.json`](#td_prefsjson-schema))
- `cache/installers/` — downloaded Derivative installers

### TouchDesigner's `pref.txt`

Not ALPACKA's file — TouchDesigner's own, shared by every build and project on the machine. ALPACKA only changes the keys you accept (see [TouchDesigner Preferences](td-preferences.md)).

| OS | Path |
|---|---|
| Windows | `%LOCALAPPDATA%/Derivative/TouchDesigner099/pref.txt` |
| macOS | `~/Library/Application Support/Derivative/TouchDesigner099/pref.txt` (unverified; used only once TD has created the folder) |

Next to it, `pref.txt.alpacka-backup` is a copy of the file from before ALPACKA first changed it.

### Per-project files

- `alpacka.yml` — the single source of truth for a project's metadata. Round-tripped via `ruamel.yaml` so comments and field ordering survive saves. Merge-saved by both ALPACKA and any AcroCore-enabled TD project. Travels with the folder.
- `alpacka.local.yml` — optional sidecar for per-machine overrides (paths, secrets). Read-only from the launcher's perspective; never written back. See [`alpacka.local.yml`](#alpackalocalyml).
- `.launcher-thumb.png` — optional thumbnail. Auto-picked up when present.

### `alpacka.local.yml`

Same fields as `alpacka.yml`, merged over it when the project loads. Keep it out of git (the `.gitignore` ALPACKA gives new projects already does). Merge rules:

- Top-level values (`name`, `launch_on_startup`, `output_mode`, …): the local value wins.
- `variables`, `variable_meta`: merged per variable.
- `run_configs`, `launch_dependencies`: merged by `name`; entries with a new name (or no name) are added.
- `kind_data.<kind>`: merged per key, and `launch_settings` inside it per setting.
- `kind` can't be changed here. A local file with a different `kind` is ignored, and Edit Details says so.

ALPACKA never copies a value from this file into `alpacka.yml`. Saving only writes the fields you changed, and Edit Details and the Auto Launch dialog show local values greyed out, so they can only be changed in this file.

## Environment variables

| Var | Purpose |
|---|---|
| `ACRO_TD_CORE` | Absolute path to the acro-td-core repo. Powers the built-in `acro-td-core` template and its `template.yml` substitution. Set by the acro-td-core `setup.ps1` / `setup.sh`. |

## `alpacka.yml` schema

```yaml
schema: 1
name: My Project
description: ""
template: acro-td-core
kind: touchdesigner            # touchdesigner | python | nodejs | shell | docker-compose | executable | generic
created: "2026-04-19T10:00:00"
last_opened: "2026-04-19T10:00:00"
tags: []
launch_on_startup: false
reopen_on_crash: false
freeze_timeout_seconds: 30
startup_grace_seconds: 60
relaunch_delay_seconds: 5
max_retries_enabled: false
max_retries: 3
output_mode: ""                # "" = use kind default; see § output_mode below
variables: {}                  # name → value, injected as env vars + run-cmd substitutions
variable_meta: {}              # name → {type, description, ...} — UI hints
run_configs: []                # named launch configurations beyond the kind default
launch_dependencies: []        # companion processes — see § launch_dependencies

kind_data:
  touchdesigner:               # only present for kind=touchdesigner
    toe_file: MyProject.toe
    alternate_toe_files: []
    td_version: "2025.32280"
    td_major: "099"
    launch_settings: {}        # launcher-owned; see § TouchDesigner launch settings
  python:                      # only present for kind=python
    entry: main.py
  # …per-kind keys for whichever kind the project is
```

Empty / default-valued fields may be omitted on disk — `project.save()` only writes fields with meaningful values, so a freshly-created project keeps `alpacka.yml` minimal. Fields that aren't modeled by the launcher (a future AcroCore version writing extra metadata, hand-written user keys) are round-tripped verbatim through `Project._raw`.


### Ownership split

- **Launcher-owned (top level)** — editable via Edit Details, written by ALPACKA: `name`, `description`, `template`, `kind`, `tags`, `launch_on_startup`, `output_mode`, `variables`, `variable_meta`, `run_configs`, the crash-relaunch fields. `created` is set once at project creation.
- **Launcher-owned (nested under `kind_data`)** — `kind_data.touchdesigner.launch_settings` is owned by the launcher even though it lives in the kind section. AcroCore dict-merges its writes so this sub-dict survives a TD save untouched.
- **Kind-owned (`kind_data.<kind>`)** — managed by the kind handler / its tool integration. For TouchDesigner: AcroCore writes `kind_data.touchdesigner.{toe_file, alternate_toe_files, td_version, td_major}` on each TD save (`toe_file` and `alternate_toe_files` are setdefault'd; the version pair always refreshes).
- **Shared** — `last_opened` is updated by whichever side most recently touched the project; last writer wins.

`project.save()` merge-saves: it loads the existing file via ruamel round-trip, mutates launcher-owned fields in place, and re-dumps. Per-kind sections in `kind_data` are dict-merged so writes from another side (AcroCore) survive a launcher save. The launcher exposes the TD-nested fields via compatibility accessors (`Project.toe_file`, `Project.td_version`, etc.) that read/write through `kind_data["touchdesigner"]`, so existing call sites don't need to know about the nesting.

### `output_mode`

Top-level field on `alpacka.yml` controlling how Launch surfaces stdout/stderr from a non-TouchDesigner project. Concrete values:

| Value | Behavior |
|---|---|
| `"console"` | Wrap the command in an external OS terminal (cmd on Windows, Terminal.app on macOS, `x-terminal-emulator` chain on Linux). Pause-on-error keeps crash output visible. |
| `"inline"` | Capture stdout/stderr into an in-app ring buffer (last 5000 lines per project). Surfaced by the "View output" card icon, which opens a modeless `OutputView` dialog. |
| `"none"` | Silent — no stdio routing (legacy behavior; right for TD and GUI executables that own their own window). |
| `""` (omitted) | "Use the kind handler's default", resolved at launch time via `Project.effective_output_mode(handler)`. |

Defaults per kind: `python`, `nodejs`, `shell`, `generic`, `docker-compose` → `"console"`; `touchdesigner`, `executable` → `"none"`.

The Edit Details page hides the picker for TouchDesigner projects since TD owns its own window + log. Inline-mode children are tied to ALPACKA's session — when the app quits, they're terminated gracefully (SIGTERM / `CTRL_BREAK_EVENT`, brief wait, SIGKILL). Console + silent launches are independent and survive ALPACKA exit.

### TouchDesigner launch settings

Launcher-owned sub-dict at `kind_data.touchdesigner.launch_settings`. Each field is optional; the on-disk block is omitted entirely when every field is at its default.

```yaml
kind_data:
  touchdesigner:
    launch_settings:
      text_console: true             # → TOUCH_TEXT_CONSOLE=1
      disable_update_check: true     # → TOUCH_NO_UPDATE_CHECK=1
      app_log_level: "Info"          # → TOUCH_APP_LOG_LEVEL=Info
      quick_crash: false             # → TOUCH_QUICK_CRASH=1 when true
      disable_crash_handlers: false  # → TOUCH_DISABLE_CRASH_HANDLERS=1 when true
      ten_bit_color: false           # → TOUCH_10_BIT_COLOR=1 when true
      double_draw: false             # → TOUCH_DOUBLE_DRAW=1 when true (legacy; prefer Window COMP)
      editor: ""                     # → TOUCH_EDITOR=<path> when non-empty
      table_editor: ""               # → TOUCH_TABLE_EDITOR=<path> when non-empty
      web_launch: ""                 # → TOUCH_WEB_LAUNCH=<browser path> when non-empty
      help_delay: null               # int seconds, or omit/null for TD default; 0 disables pop-ups
      midi_chan_override: ""         # space-separated triple, e.g. "0 2 4"
      extra_args: ["-gpuAffinity", "1"]  # injected between td.exe and the .toe path
```

| Field | Type | TD effect |
|---|---|---|
| `text_console` | bool | Opens TD's internal text-console window at launch (`TOUCH_TEXT_CONSOLE`). |
| `disable_update_check` | bool | Skips the update check on launch (`TOUCH_NO_UPDATE_CHECK`). |
| `app_log_level` | string | App log verbosity. Empty = TD default. Common values: `Off`, `Error`, `Warning`, `Info`, `Debug` (`TOUCH_APP_LOG_LEVEL`). |
| `quick_crash` | bool | Crash without the dialog (`TOUCH_QUICK_CRASH`). |
| `disable_crash_handlers` | bool | Disable TD's crash handlers (`TOUCH_DISABLE_CRASH_HANDLERS`). |
| `ten_bit_color` | bool | Enable 10-bit color output (`TOUCH_10_BIT_COLOR`). Requires a hi-color LCD on DisplayPort. |
| `double_draw` | bool | Output every rendered frame twice (`TOUCH_DOUBLE_DRAW`). Legacy — TD recommends the Window COMP's parameter instead. |
| `editor` | string | Path to the external text editor TD uses for DAT/script editing (`TOUCH_EDITOR`). Per-machine. |
| `table_editor` | string | Path to the external table editor (`TOUCH_TABLE_EDITOR`). Per-machine. |
| `web_launch` | string | Path to an alternate browser TD should use when opening help / docs links (`TOUCH_WEB_LAUNCH`). Empty = system default browser. Per-machine. |
| `help_delay` | int (seconds) or null | Pop-up help delay in **seconds**. `null`/absent = TD default. `0` = disable pop-up help entirely (a meaningful explicit value, distinct from absent). Any positive int = that many seconds (`TOUCH_HELP_DELAY`). |
| `midi_chan_override` | string | Space-separated triple `"<input1> <input2> <output>"`, e.g. `"0 2 4"` (`TOUCH_MIDI_CHAN_OVERRIDE`). Empty = no override. |
| `extra_args` | list[string] | Raw CLI args appended after `td.exe` and before the `.toe` path. Use for the multi-GPU affinity flags from TD's "Using Multiple Graphic Cards" docs. |

All fields except `extra_args` surface as typed widgets in the **TouchDesigner Launch** section of Edit Details. `extra_args` is YAML-only. Per-machine values (`editor`, `table_editor`, `extra_args` for GPU indices) are best kept in [`alpacka.local.yml`](#alpackalocalyml) rather than the committed project file. The local-override merge handles `kind_data.touchdesigner.launch_settings` per key, so a sidecar like:

```yaml
# alpacka.local.yml
kind_data:
  touchdesigner:
    launch_settings:
      extra_args: ["-gpuAffinity", "2"]
```

…overlays `extra_args` while leaving any committed `text_console` / `disable_update_check` etc. intact.

For any TOUCH_* var not on the curated list, or to override one with a value the UI doesn't expose, use a run_config with an explicit `env` map — see `run_configs[].env` below. Run-config env wins over `launch_settings`-derived env when both set the same key.

### `run_configs[].env`

Generic, kind-agnostic environment-variable map on a named run configuration. Layered onto the spawned process's environment after the project's `variables` and any kind-specific structured settings (e.g. TD's `launch_settings`), so it acts as the most-explicit override.

```yaml
run_configs:
  - name: default
    env:
      TOUCH_DOUBLE_DRAW: "1"
      MY_DEBUG_FLAG: "verbose"
```

Values are coerced to str at launch time. Empty / missing keys are not set.

### `launch_dependencies`

Top-level list of companion commands ALPACKA spawns alongside the main launch — sidecar services, dev servers, helper scripts. Kind-agnostic: any project, regardless of `kind`, can declare them. Each entry:

```yaml
launch_dependencies:
  - name: "redis"                          # optional human label
    command: "redis-server --port 6380"    # required, shlex-split, supports {VAR}
    enabled: true                          # default true
    oneshot: false                         # default false (persistent)
    keep_alive: false                      # default false (kill when main exits)
```

| Field | Type | Notes |
|---|---|---|
| `name` | string (optional) | Display label for Edit Details + the inline-output `[<source>]` prefix. Falls back to the dep's index when blank. |
| `command` | string (required) | Shell-style command line. Parsed via `shlex.split(posix=True)`. Supports `{VAR}` / `${VAR}` substitution from the project's `variables`. Empty rows are dropped on save. |
| `enabled` | bool (default `true`) | Uncheck to skip launching a dep without deleting the row. Disabled rows are stored but never spawned. |
| `oneshot` | bool (default `false`) | The process exits on its own. ALPACKA records its exit code via `mark_completed`; the row stays in `running_processes` with `completed_at` set so Edit Details can show `✓ done` / `✗ exit N` until the next launch. Persistent (default) deps live until terminated. |
| `keep_alive` | bool (default `false`) | When the main project process dies (clean or crashed), persistent deps with this flag stay running. Default false: deps die with main, mirroring the user's mental model of "stop the project." Stop on the project card always terminates everything regardless. |

Behavior at launch:

- Spawned **after** the main process so a dep failure can't poison the main launch's bookkeeping. Each dep's command is `{VAR}`-substituted, then `shlex.split` into argv.
- `cwd` defaults to the project folder. `env` is the project's variables merged onto the parent's `os.environ`, the same env layering main launches see.
- `output_mode` is inherited from the project. `silent` (default for TD): no stdio capture. `inline`: each dep's stdout is captured into the project's shared `OutputBuffer` with a `[<label>] ` prefix on every line, so deps and main interleave chronologically in the OutputView dialog. `console`: each dep gets its own terminal window.
- `process_name_pattern` is derived live from `psutil.Process(pid).name()` (lower-cased, `.exe` stripped) at spawn time so the PID-reuse name-check works for wrapped commands like `python -m worker` (real proc name `python`). Fallback: `Path(argv[0]).stem.lower()`.
- A dep that fails to spawn (e.g. `redis-server` not on PATH) logs to stderr and the launch continues — the user sees "– not spawned" in Edit Details for that row. The main launch is never blocked by a dep failure.

Behavior on exit / Stop:

- **Stop button** on the project card is project-scoped: `terminate_project_processes` kills deps first (so a dep talking to main doesn't spam connection-refused on the way down), then the main. The prompt counts both ("Terminate all 3 running processes…").
- **Main process dies** (clean exit, crash, or external kill): the crash monitor's `tick` calls `_terminate_dependents`, which tears down all deps without `keep_alive`. If the project also has `reopen_on_crash`, the relaunch goes through `_spawn` again and respawns deps fresh — surviving `keep_alive` deps don't block the relaunch's race-guard.
- **Dep dies on its own**: not treated as a project crash event. `sweep_dead` removes its row (or, for `oneshot`, leaves it in place with `completed_at` set so the UI can show the exit code).

Local override: `alpacka.local.yml` can name-merge entries into `launch_dependencies` the same way it merges `run_configs` — useful for per-machine dep paths or to disable a dep without editing the committed yml.

## `settings.json` schema

```json
{
  "td_installs": [
    {"version": "2025.32280", "major": "099", "path": "C:/Program Files/Derivative/TouchDesigner/bin/TouchDesigner.exe"}
  ],
  "default_td_install": "2025.32280",
  "theme": "dark"
}
```

## `td_prefs.json` schema

Owned by `td_prefs.py`. Kept out of `settings.json` so a launch-time write can't be overwritten by the main window's next settings save.

```json
{
  "version": 1,
  "managed": {"general.playbarshortcuts": "0", "dats.texteditor": "C:/Users/me/AppData/Local/Programs/Microsoft VS Code/Code.exe"},
  "declined": ["general.saveprompt"],
  "files": {
    "SHOW-PC-01|C:/Users/me/AppData/Local/Derivative/TouchDesigner099/pref.txt": {
      "originals": {"general.playbarshortcuts": "1", "dats.texteditor": null},
      "written": {"general.playbarshortcuts": "0", "dats.texteditor": "C:/Users/me/AppData/Local/Programs/Microsoft VS Code/Code.exe"}
    }
  },
  "last_result": {"status": "applied", "source": "launch", "at": "2026-11-03T14:02:11+00:00", "changes": [], "message": ""}
}
```

| Field | Notes |
|---|---|
| `managed` | Keys you accepted, with the value ALPACKA keeps them at. Set before every TD launch. |
| `declined` | Keys you unticked or ignored. The first-run prompt only asks about recommended keys in neither list, so a new recommendation in a later version gets asked about on its own. |
| `files` | Per machine and file (`<hostname>\|<path>`), so a portable copy moved to another machine doesn't restore the wrong values. `originals` is each key's value before ALPACKA first changed it (`null` = the line wasn't there, so reverting deletes it). `written` is what ALPACKA last set; a different value in `pref.txt` means it was changed outside ALPACKA. |
| `last_result` | The most recent attempt: `applied`, `up_to_date`, `waiting` (a TouchDesigner was running), or `error`. Shown in Settings. |

## SQLite schema

### `projects`

```sql
CREATE TABLE projects (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    added_at TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    last_launched TEXT,
    stale INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'touchdesigner',
    approved_run_hash TEXT
);
```

`approved_run_hash` is the SHA-256 of the project's run settings as last approved on this machine (see [Reviewing what a project runs](library.md#reviewing-what-a-project-runs)). `NULL` = never approved; `pending-upgrade` = in the library before approval existed, approved at the next startup.

### `templates`

```sql
CREATE TABLE templates (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    toe_path TEXT NOT NULL,
    builtin INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'touchdesigner'
);
```

### `running_processes`

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
    create_time REAL
);
```

A project can have multiple rows: one for the main process plus one per spawned launch dependency. Deps register with `kind="dependency"` and `run_config_name="dep:<index>"` so the Edit Details view can correlate each row back to its yml entry. The `toe_file` column is repurposed for dep rows to carry the dep's command text (it's the schema's generic "artifact filename" slot per `kinds/base.py::LaunchSpec.artifact_filename`).

| Schema bump | Adds |
|---|---|
| v3 | `kind`, `process_name_pattern`, `run_config_name`, `exit_code`, `completed_at` — multi-kind support + one-shot exit recording |
| v4 | `output_mode` — terminate / shutdown can branch on inline-vs-console without re-deriving from the project |
| v5 | `keep_alive_after_main`, `oneshot` — launch dependency lifecycle flags driving the crash monitor's tear-down rules |

Schema migrations are additive — `db.py` walks the `user_version` pragma forward, applying `_migrate_v{N}_to_v{N+1}` add-column steps as needed.
