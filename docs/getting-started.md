# Getting Started

## Install

### From source (dev)

```bash
git clone https://github.com/acronym-lol/acro-alpacka.git
cd acro-alpacka
python -m venv .venv
.venv/Scripts/activate         # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -e .
python -m alpacka
```

### From a built release

Grab the latest release from **[github.com/acronym-lol/acro-alpacka/releases/latest](https://github.com/acronym-lol/acro-alpacka/releases/latest)**:

- **Windows (most users)** — `ALPACKA-<version>-windows-x64-setup.exe`. Runs a per-user install, adds a Start Menu entry, and registers an uninstaller in Add/Remove Programs.
- **Windows (portable)** — `ALPACKA-<version>-windows-x64-portable.zip`. Extract anywhere, run `ALPACKA.exe`. No install, no registry entries.
- **macOS** — `ALPACKA-<version>-macos-arm64.dmg`, for Apple silicon Macs (M1 or later). Mount it, drag `ALPACKA.app` into the Applications alias.

Config lands in the per-user location by default — see [Configuration](configuration.md) for paths.

#### First-run warnings

ALPACKA isn't code-signed yet (paid certs aren't in scope for v1). Both OSes show a one-time security prompt on first launch:

- **Windows**: "Windows protected your PC" (SmartScreen). Click **More info** → **Run anyway**.
- **macOS 15 or later**: the first time you open `ALPACKA.app`, macOS says it could not verify it. Click **Done**, then go to **System Settings → Privacy & Security** and click **Open Anyway** next to the ALPACKA message.
- **Older macOS**: **Right-click** (or `Ctrl`-click) the app → **Open** → **Open**.

Second launch onwards is normal.

### Portable mode

Drop an empty file named `portable.txt` next to the `ALPACKA` executable. Config + DB then live in `./data/` alongside the exe, so the whole install travels on a USB stick. Works with either the installer-based install or the portable zip.

## First run

On first launch ALPACKA:

1. Creates its config folder (`%APPDATA%/ALPACKA/` on Windows, `~/Library/Application Support/ALPACKA/` on macOS).
2. Initializes the SQLite database at `projects.db`.
3. Scans for installed TouchDesigner versions via the Windows registry (file-association entries) or `/Applications/TouchDesigner*.app` on macOS. Detected installs appear in Settings.
4. Seeds the built-in templates: `acro-td-core` (points at `$ACRO_TD_CORE/AcroCore.toe`), `blank` (TD's bundled `Samples/Setup/Base/NewProject.toe`), `example` (TD's `Samples/Setup/Example/NewProject.toe`).

If `ACRO_TD_CORE` is unset, the `acro-td-core` built-in shows a "source missing" warning — set the env var to your [acro-td-core](https://github.com/acronym-lol/acro-td-core) clone to enable it.

## Create your first project

1. Click **New Project** in the top bar.
2. Pick a kind (TouchDesigner, Python, NodeJS, Docker-Compose, Shell, Executable, Generic). Each kind exposes its own setup fields below.
3. Give it a name, choose a parent folder, pick a template (TD only — other kinds skip straight to scaffolding).
4. For TD: pick a version (pre-filled with your default install). For Python/NodeJS: optionally toggle "Install dependencies after create" to run the kind's install steps (pip / npm) once the folder is scaffolded.
5. Optional fields: description, comma-separated tags, init git, README, remote URL, launch immediately.
6. Click **Create**.

ALPACKA creates the project folder, runs universal bootstrap (per-kind folder set + baseline `.gitignore`), copies the template (TD: a `.toe`; other kinds: any files declared in the template's `template.yml`), writes a fresh `alpacka.yml`, runs any scaffold + install steps the kind contributes, then optionally launches.

## Add an existing project

Click **Add Existing** → pick the folder. ALPACKA detects the project kind from on-disk signals (`pyproject.toml`/`requirements.txt` → python, `package.json` → nodejs, `docker-compose.yml` → docker-compose, `*.toe` → touchdesigner, …), writes or updates `alpacka.yml`, and registers the project in the library.

## Customizing a project

Click the kebab on a card → **Edit Details** to open the full-page editor. From there you can change the name / description / tags, set per-project environment variables, edit the run command (or, for TD, toggle the curated set of `TOUCH_*` flags), choose an `output_mode` for non-TD kinds, and declare [launch dependencies](configuration.md#launch_dependencies) — companion commands ALPACKA spawns alongside the main launch (Redis sidecar, OSC bridge, `npm run dev`, etc.) with per-dep one-shot / keep-alive flags. Flag a project for `launch_on_startup` + `reopen_on_crash` from the autostart settings dialog (gear icon next to the kebab) and ALPACKA will auto-launch it on boot and respawn it after crashes or hung-window detection.
