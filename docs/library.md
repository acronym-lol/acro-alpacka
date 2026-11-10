# Project Library

The **Library** view is ALPACKA's home screen: a grid of project cards sourced from a SQLite registry (`projects.db → projects`), enriched with the `alpacka.yml` that lives inside each project folder.

## Card layout

```
┌─ optional thumbnail / kind-icon placeholder ───┐
│                                                │
├────────────────────────────────────────────────┤
│ ★ ⚡  PROJECT NAME                  [⚙] [⋯]   │   title row (pin, auto-start badge, name, autostart-settings, kebab)
├────────────────────────────────────────────────┤
│ Last opened 2h ago        TD 2025.32280       │   meta row (kind-specific target line)
│ description paragraph                          │
│ ☑ main.toe                                     │   target row — TD: per-toe checkboxes;
│ ☐ tools.toe                                    │     other kinds: run-config picker
│                                                │
│ [📁] [</>] [>_] [📜] [GH] [🌐]                 │   external-tool row
│ [ ▶ LAUNCH ─────────────── ]                  │   run row (idle)
└────────────────────────────────────────────────┘
```

When a project is running, the run row becomes:

```
│ [ ● RUNNING (3) ────── ][■]                   │
```

- Clicking the button **focuses** the existing window (TD: TD itself; other kinds: best-effort raise via `kinds.base.default_focus`).
- The inline square button is project-scoped Stop — terminates the main process *and* every tracked launch dependency, in that order. Confirmed by a frameless dialog that counts the processes.
- The count in parentheses includes launch dependencies. A project running TD plus two deps shows `RUNNING (3)`.
- The `⋯` menu gains **Launch new instance** so you can spawn a second copy explicitly.

## External-tool row

Order of icons on the card's tool row is fixed; conditional buttons appear only when relevant:

| Icon | Shown when | Action |
|---|---|---|
| 📁 folder | always | Open project folder in OS file manager |
| `</>` VS Code | always | Open project root in VS Code |
| `>_` terminal | always | Open OS terminal in project folder |
| 📜 logs | non-TD project with effective `output_mode == "inline"` | Open the modeless OutputView dialog tailing captured stdout/stderr (interleaved main + deps) |
| GH | git remote detected | Open the repo's origin URL in the browser |
| 🌐 globe | the kind handler's `display_url(project, variables)` returns one (e.g. nodejs computes `http://localhost:{PORT}` from `variables`) | Open that URL in the browser |

## State signals on cards

| Signal | Where | Color |
|---|---|---|
| Pinned | Filled MARIGOLD pushpin icon (left of name) | MARIGOLD `#FDCF00` |
| Launch on startup | Green circular badge w/ zap icon (left of name) | MATRIX GREEN `#07A400` |
| Running | Solid-green Launch button + red Stop | MATRIX GREEN + BLAZE |
| Stale | ⚠ badge (folder or `.toe` missing) + disabled Launch | BLAZE `#DB5400` |
| Needs review | Amber **REVIEW & LAUNCH** button (see below) | AMBER `#FF8A2F` |

## Reviewing what a project runs

A project's `alpacka.yml` (and `alpacka.local.yml`) can make ALPACKA run commands: run configs, launch dependencies, an executable path, environment variables, TouchDesigner arguments and editor paths, and auto-launch at startup. A project folder can come from anywhere — a git clone, a shared drive — so before ALPACKA runs those settings for the first time on a machine, **Launch shows exactly what will run and asks you to approve it**. It asks again whenever they change, for example after a `git pull`.

- **Approve and Run** records the approval for this machine (in the library DB) and launches.
- **Auto-launch and crash relaunch never run unreviewed settings.** The project is skipped and a banner at the top of the window names it.
- **No prompt for:**
  - **A TouchDesigner project that only opens its own `.toe`.** There's nothing extra to review.
  - **Projects you create in ALPACKA, or add from a folder with no `alpacka.yml`.** They're approved when they're set up.
  - **Edits made in Edit Details or the Auto Launch dialog.** These keep an existing approval current.
  - **Projects already in the library when you updated ALPACKA.** They were approved as they were at the update.
- **What approval can't do:** opening a `.toe` runs that project's Python, and `npm` / `pip` run a package's scripts. Approval covers what the project's ALPACKA settings add on top. Only open projects you trust.

Changes that run nothing — name, description, tags, the TD version AcroCore records — never trigger a review.

## Library toolbar

- **Search** — filters by project name, description, tags, template name, and path.
- **Sort** — Recent (pinned first, then most-recently-launched), Name, Added.
- **Density toggle** — flips between expanded (with thumbnail / description) and compact (no thumbnail) cards. Persisted in `settings.json`.
- **Refresh** — re-scans disk (folder + `.toe` existence) and re-sweeps the process tracker.

## Responsive grid

The card grid recomputes columns on window resize based on a minimum card width of 280px and 12px gaps, capped at 5 columns. Cards have drop shadows for depth; pinned cards get a warm MARIGOLD-tinted shadow and a MARIGOLD border. The library content area sits in a recessed `gray-05` well with a faint grid background, so cards read as lifted surfaces.

## Tag tiles

The content area doubles as a tag-tree browser. At root, every root-level tag renders as a flow-laid-out tile (icon + project count + child count) above the project grid; clicking drills down. Tiles double as drag/drop targets — drop a project card onto a tile to add the tag, drop a tile onto another tile to nest it. Inside a tag, a breadcrumb appears at the top with click-back-to-root.

## Right-click / ⋯ menu

Per card: Edit Details, Pin / Unpin, Remove from Library (never touches the folder on disk), and — when running — Launch new instance. The autostart settings gear (`⚙` next to the kebab) opens a modal for `launch_on_startup`, `reopen_on_crash`, freeze timeout, retry cap, and relaunch delay — all stored on the project's `alpacka.yml`.

**Auto Relaunch on Close** works for every kind of project. Whenever the project's process ends for any reason except **Stop** — a crash, a clean exit, or a run that finishes on its own — ALPACKA starts the same `.toe` or run config again after the relaunch delay, up to the retry cap if one is set. With Auto Relaunch on, a project that uses console output doesn't keep its window open waiting for a key after an error, so the relaunch isn't held up.
