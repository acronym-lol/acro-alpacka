# Templates & `template.yml`

ALPACKA's templates are just `.toe` files with optional declarative setup config. Built-in templates ship with the repo; user-added templates can be any `.toe` path on disk.

## Built-ins

| Name | Source | template.yml? |
|---|---|---|
| `acro-td-core` | `$ACRO_TD_CORE/AcroCore.toe` | yes — at `$ACRO_TD_CORE/template.yml` |
| `blank` | `<TD install>/Samples/Setup/Base/NewProject.toe` | no — universal bootstrap only |
| `example` | `<TD install>/Samples/Setup/Example/NewProject.toe` | no |

Built-ins are re-seeded on every launcher startup; their `toe_path` auto-refreshes to track the current environment.

## User templates

Templates view → **Add Template…** → pick any `.toe`, name it. Stored in the `templates` table. User templates can gain rich behavior by dropping a sidecar yml next to them.

## Template resolution

When creating a project from a template, ALPACKA looks for a `template.yml` in two places:

1. `<toe_path>.parent / "template.yml"` — directory form (covers the built-in acro-td-core template, where the whole repo root *is* the template dir).
2. `<toe_path>.stem + ".template.yml"` sibling — sidecar form (for user templates that want to opt in).

If no config is found, the project still gets the [universal bootstrap](#universal-bootstrap) — folders + baseline `.gitignore`.

## Universal bootstrap

Every new project, regardless of template, gets:

- Folders: kind-specific (TD: `scripts/`, `content/`, `tox/`, `TDLogs/`; non-TD kinds ship their own list or none — see `KindHandler.universal_folders`)
- Baseline `.gitignore` (bundled at `src/alpacka/resources/universal.gitignore`)
- `alpacka.yml`

Can be toggled via the "Create basic folder structure" checkbox in the New Project dialog.

## `template.yml` schema (v1)

```yaml
schema: 1
name: acro-td-core                     # optional — overrides DB display name
description: "AcroCore framework..."   # optional — overrides DB description
toe: AcroCore.toe                      # .toe filename, relative to template.yml

folders:                               # extra folders beyond the universal set
  - macros/main

files:                                 # declarative file copies
  - src: .gitignore                    # paths relative to template.yml
    dst: .gitignore
    overwrite: true                    # override baseline universal .gitignore
  - src: assets/CLAUDE.md.template
    dst: CLAUDE.md
    substitute: true

trees:                                 # recursive directory copies
  - src: .claude/skills
    dst: .claude/skills
    overwrite: true                    # refresh on every create

substitutions:
  PROJECT_NAME: "{{project:name}}"
```

### Fields

| Key | Type | Notes |
|---|---|---|
| `schema` | int | Always `1` for v1. Bumped when incompatible changes land. |
| `name` | str (optional) | Overrides DB display name. |
| `description` | str (optional) | Overrides DB description. |
| `toe` | str | Filename of the template `.toe`, relative to the config. |
| `folders` | list[str] | Extra folders to `mkdir -p` under the project root. |
| `files` | list[FileEntry] | Declarative copies — see below. |
| `trees` | list[TreeEntry] | Recursive directory copies — see below. |
| `substitutions` | dict[str, str] | Values available in files marked `substitute: true`. |

### `FileEntry`

```yaml
- src: <path>       # relative to template.yml
  dst: <path>       # relative to the new project folder
  substitute: false # run {{KEY}} replacement through the content
  overwrite: false  # replace dst if it already exists
```

Default `if_missing: true` (skip when dst exists); flip via `overwrite: true`.

### `TreeEntry`

```yaml
- src: <dir>
  dst: <dir>
  overwrite: false  # if true, replace dst files even when they already exist
```

Trees walk `src` recursively and copy each file into `dst`, creating
parent dirs as needed. Existing files in `dst` are kept by default —
flip `overwrite: true` to replace them. This makes `dst: .` (the
project folder, which always exists by the time templates apply) a
safe target, and lets a tree coexist with `files:` entries that
already wrote substituted versions of select files: those stay, and
the tree fills in everything else around them.

### Substitutions

Two-layer substitution engine:

**Layer 1 — mapping values** in `substitutions:` use `{{source:name}}`:

| Source | Resolves to |
|---|---|
| `project:name` | the new project's name, free-form ("Test Python CLI 2") |
| `project:slug` | the project's name lower-kebab-cased ("test-python-cli-2") for fields with parser/registry constraints (PEP 621 names, npm package names, TOML `[project.scripts]` bare keys) |
| `project:folder` | absolute path (forward slashes) |
| `project:toe_file` | `<name>.toe` |
| `var:NAME` | the project variable `NAME` (declared under `variables:`) |

There's no `env:` source: a template can't read environment variables, because a template from someone else could copy a token into the files it generates. Pass machine-specific values in as variables instead.

**Layer 2 — template files** (where `substitute: true`) reference the keys as `{{KEY}}` — all-caps, no source prefix:

```json
{"name": "{{PROJECT_NAME}}"}
```

Unknown `{{KEY}}` in a template file raises at apply time — catches typos before shipping broken files. Unknown sources (not `project:` or `var:`) likewise fail loudly.

### Paths stay inside their folders

Every `folders` entry and `dst` must stay inside the new project folder, and every `src` inside the template's own folder: absolute paths, `..` that climbs out, and symlinks are refused (the create fails with a clear error). Symlinks inside a `trees:` source, or in a folder template, are skipped. A template's own `alpacka.local.yml` is never copied. Templates can come from anyone, so a template can't write outside the project or read files elsewhere on the machine.

Installing dependencies after create runs the template's own `pip` / `npm` scripts, so for templates that aren't built in, **Install dependencies** starts unticked.

## Example: acro-td-core

Lives at the root of the [acro-td-core](https://github.com/acronym-lol/acro-td-core) repo:

```
acro-td-core/
├── template.yml          # <-- the ALPACKA template config
├── AcroCore.toe          # the template .toe
├── .gitignore            # shared with the repo itself AND copied to user projects
├── .claude/skills/       # shared (Claude Code skills shipped with the framework)
└── assets/
    ├── CLAUDE.md.template
    └── .mcp.json.template
```

The template.yml declares: copy `.gitignore` + `.claude/skills` verbatim, copy the two `assets/*.template` files with substitutions for `ACRO_TD_CORE`, and add `macros/main/` to the folder set.
