# ALPACKA — Acronym Launcher: Projects And Creative Kits Automator

Desktop launcher for managing creative-tool projects and services. Initially built around TouchDesigner, designed to scale to additional toolchains and long-running services over time.

## What it does

- **Project library** — persistent, searchable catalog of your projects with pinning, tags, thumbnails, and metadata that travels with each project folder.
- **Templated project creation** — create new projects from templates that can declare their own setup via a `template.yml` (folders, files, substitutions, recursive copies, post-create install steps).
- **Multiple project kinds** — first-class TouchDesigner support, plus Python, NodeJS, Docker-Compose, shell, executable, and generic kinds. Each kind brings its own launch surface (entry / script / command / argv) and post-create install behavior.
- **TD version pinning** — each TD project remembers which TouchDesigner build it was saved with; Launch opens it in that exact version (or offers to install / fall back to a newer build).
- **Live process tracking** — ALPACKA tracks every process it spawns. Cards show `● RUNNING` while live, clicking the button focuses the existing window instead of double-launching, and a one-click Stop terminates everything for the project.
- **Launch dependencies** — per-project companion commands (Redis sidecar, OSC bridge, `npm run dev`, …) that ALPACKA spawns alongside the main launch and tracks live, with per-dep one-shot / keep-alive / output-capture flags.
- **Output capture** — non-TD projects can route stdout/stderr to an in-app log viewer (inline mode), a real OS terminal (console mode), or run silently. Inline mode interleaves main + dependency output in the same scrollback.
- **Launch-on-startup + crash relaunch** — per-project opt-in. Flagged projects auto-open when ALPACKA boots; a 6-second sweep relaunches them on crash or hang (Windows hung-window detection) within a configurable retry cap.
- **External tool integrations** — jump to a project's folder, open it in VS Code, open a terminal there, view captured output, or open its git origin / kind-published display URL in the browser.

## Where to go next

- **[Getting Started](getting-started.md)** — installing ALPACKA, first-run experience, creating your first project.
- **[Architecture](architecture.md)** — the module layout, the SQLite schema, the launch pipeline, how ALPACKA talks to TD.
- **[Project Library](library.md)** — card anatomy, state signals, tag tree, toolbar, and reviewing what a project runs before it first runs.
- **[Templates & template.yml](templates.md)** — the declarative template-config system, schema reference, substitutions.
- **[Launch Dependencies](launch-dependencies.md)** — companion processes spawned alongside the main launch (sidecars, dev servers, helper scripts).
- **[Process Tracking](process-tracking.md)** — how running state is captured, persisted, and surfaced; kind-dispatched liveness; crash monitor.
- **[TouchDesigner Preferences](td-preferences.md)** — the TD preferences ALPACKA can keep set for show machines, and how it changes `pref.txt` safely.
- **[Configuration](configuration.md)** — all the file paths, environment variables, and on-disk formats. The `alpacka.yml`, `launch_dependencies`, and `running_processes` schemas live here.
- **[Development](development.md)** — building from source, tests, packaging, conventions and the safety rules contributors must keep.
