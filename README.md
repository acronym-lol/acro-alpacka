# ALPACKA — Acronym Launcher: Projects And Creative Kits Automator

A desktop launcher for managing TouchDesigner projects and (eventually) other creative-tool services. Written in Python + PySide6, packaged as a portable desktop app for Windows and macOS.

## What it does

- **Project library** — a persistent catalog of your TouchDesigner projects with pinning, search, tags, templates, and per-project metadata stored alongside each `.toe`.
- **Templated project creation** — create new projects from a built-in Acro TD Core template (full framework) or Blank / Example (TD's bundled starter scenes). Templates can declare their own setup via a `template.yml` — folder structure, files to copy, substitutions.
- **Version pinning** — each project remembers which TD build it was saved with; clicking Launch opens it in that exact version (with a fallback flow if the version isn't installed).
- **Live process tracking** — the launcher tracks which TD processes it spawned, flips the card button to `● RUNNING`, focuses the existing window instead of launching twice, and can terminate processes from the card.
- **Launch on startup** — opt-in per project. Projects flagged for auto-launch open automatically when ALPACKA boots.
- **External tool integrations** — jump to the project folder, open it in VS Code, open a terminal there, or open the associated git repo in the browser.

## Download

Pre-built releases: **[github.com/acronym-lol/acro-alpacka/releases/latest](https://github.com/acronym-lol/acro-alpacka/releases/latest)**

Pick the asset that matches your platform:

| Platform | File | Notes |
| --- | --- | --- |
| Windows | `ALPACKA-<version>-windows-x64-setup.exe` | Installer. Per-user install, Start Menu entry, uninstaller. **Most users want this.** |
| Windows | `ALPACKA-<version>-windows-x64-portable.zip` | Portable folder — extract anywhere, run `ALPACKA.exe`. No install. |
| macOS | `ALPACKA-<version>-macos.dmg` | Mount, drag `ALPACKA.app` into Applications. |

### First-run warnings

ALPACKA isn't code-signed yet, so the first launch shows an OS security prompt. Each release includes `SHA256SUMS-windows.txt` and `SHA256SUMS-macos.txt` so you can check a download matches what was published. The prompt is one-time:

- **Windows — SmartScreen**: "Windows protected your PC" dialog. Click **More info**, then **Run anyway**.
- **macOS — Gatekeeper**: double-click won't work the first time. **Right-click** (or `Ctrl`-click) `ALPACKA.app` → **Open** → confirm **Open** in the prompt. Subsequent launches work normally.

## Development setup

```bash
# Clone + venv
git clone https://github.com/acronym-lol/acro-alpacka.git
cd acro-alpacka
python -m venv .venv
.venv/Scripts/activate                 # Windows
# source .venv/bin/activate             # macOS / Linux

# Install the package in editable mode (with test dependencies)
pip install -e ".[dev]"

# Run
python -m alpacka

# Tests
pytest
```

The `ACRO_TD_CORE` environment variable should point at your [acro-td-core](https://github.com/acronym-lol/acro-td-core) checkout so the built-in "Acro TD Core" template resolves. ALPACKA auto-detects TD installations from the Windows registry (file-association entries) or `/Applications` on macOS — no config needed beyond that.

## Build locally

```bash
pip install -r requirements.txt       # pulls in PyInstaller
python build.py
# Output: dist/ALPACKA/ — copy this folder anywhere, run ALPACKA.exe / ALPACKA.app
```

For release artifacts (Windows installer, portable zip, macOS DMG) CI does the packaging — see `.github/workflows/release.yml`. Push a `v*` tag and the release is built and published automatically.

## Config location

Runtime state (SQLite DB of your project library, settings, caches):

- Windows: `%APPDATA%/ALPACKA/`
- macOS: `~/Library/Application Support/ALPACKA/`
- **Portable mode**: drop an empty `portable.txt` next to the ALPACKA executable — config lands in `./data/` instead, so the whole app moves with a USB stick.

## Docs

See `docs/` for deeper reference:

- Architecture
- Template config system (`template.yml` schema)
- Process tracking internals
- Configuration & file locations
- Development: conventions, safety rules, packaging

The same docs are published at **[acronym-lol.github.io/acro-alpacka](https://acronym-lol.github.io/acro-alpacka/)**.

## Security

ALPACKA runs other people's project folders and templates, so it asks before running a project's commands for the first time on a machine. To report a security problem, see [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE). ALPACKA bundles third-party software (Qt / PySide6 under the LGPL, psutil, PyYAML, ruamel.yaml, Lucide icons); see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
