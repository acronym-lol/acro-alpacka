# Bundled Templates

Templates shipped with ALPACKA live here. Each template is a folder under
`resources/templates/<kind>/<name>/` containing a `template.yml` plus
(optionally) a `files/` subdirectory with the source tree. Discovery runs
on every launcher startup: each `template.yml` found in this tree gets
upserted into the templates DB with `builtin=1` under the matching kind.

## Folder convention

```
resources/templates/
├── nodejs/
│   ├── sveltekit/
│   │   └── template.yml            # scaffold-command template (no files/)
│   └── vite-react/
│       ├── template.yml
│       └── files/                  # file-copy template
│           ├── package.json
│           └── src/main.tsx
├── python/
└── shell/
```

**Two shapes:**

1. **File-copy templates** (`template.yml` + `files/` subdir). The yml's
   `files:` / `trees:` entries reference paths relative to the template
   root (so `src: files/package.json` resolves correctly). No external
   dependencies at create time.

2. **Scaffold-command templates** (`template.yml` only, with a
   `scaffold:` block). ALPACKA runs an external CLI — `npx sv create`,
   `npm create vite`, `cookiecutter`, etc. — and the scaffolder produces
   the tree. Requires the declared `requires_command:` on the user's
   PATH at create time.

Both shapes can coexist in a single template: scaffold first, then
file-copy overlays (a README, a `.env.example`, etc.).

## Name uniqueness

The `templates.name` column is `UNIQUE` in the database. Bundled
template names must not collide with the hardcoded TD built-ins
(`acro-td-core`, `blank`, `example`) or with other bundled
templates. Keep names lowercase + hyphen-separated by convention
(`sveltekit`, `vite-react`, `express-api`).

## template.yml schema

```yaml
schema: 1
kind: nodejs                         # must match the parent folder name
name: sveltekit                      # unique across the whole templates table
description: "Short one-liner shown in the New Project dialog."

# --- File-copy actions (optional) --------------------------------------
folders:                             # mkdir these inside the new project
  - src
  - src/routes

files:                               # copy, optionally with substitution
  - src: files/package.json
    dst: package.json
    substitute: true                 # replace {{KEY}} tokens
    overwrite: true

trees:                               # recursive directory merge
  - src: files
    dst: .
    overwrite: false                 # keep existing files (true = clobber per file)

substitutions:                       # values used when substitute: true
  PROJECT_NAME: "{{project:name}}"   # sources: {{project:*}} / {{var:*}}

# --- Variables (optional) ----------------------------------------------
# Persisted into the new project's alpacka.yml. Prompted in the New
# Project dialog. Types: string, int, float, bool, path, enum, multiselect.
variables:
  - name: PORT
    type: int
    default: 5173
    prompt: "Dev server port"
  - name: FEATURES
    type: multiselect
    options:
      - {value: tests, label: "Unit tests"}
      - {value: lint, label: "Linter"}
    default: [tests]

# --- Scaffold block (optional; bundled-only) ---------------------------
# Runs an external CLI at create time. User-added templates CANNOT
# declare a scaffold block — parsed only when the template is loaded
# with `trusted=True` (i.e., discovered from resources/templates/).
scaffold:
  requires_command: npx              # preflight: shutil.which must find it
  command: "npx --yes sv@latest create --template {SVELTE_TEMPLATE} ."
  cwd: "."                           # relative to the project folder
  inputs:                            # scaffold-only vars, fed to the CLI
    - name: SVELTE_TEMPLATE
      type: enum
      options: [minimal, demo, library]
      default: minimal

# Dependency install runs automatically based on the project's `kind`
# (Python: venv create + `pip install -e .`; NodeJS: `<pm> install`;
# etc.) — no per-template configuration needed. The New Project
# dialog gates it behind an "Install dependencies after create"
# checkbox; failure leaves the project registered for manual retry.
```

## Substitution sources

In `substitutions:` values, ALPACKA resolves these reference forms:

| Form                       | Resolves to                                           |
|----------------------------|-------------------------------------------------------|
| `{{project:name}}`         | The project's name, free-form ("Test Python CLI 2")   |
| `{{project:slug}}`         | The project's name lower-kebab-cased ("test-python-cli-2"). Use in fields with parser/registry constraints: PEP 621 `[project] name`, npm package name, TOML `[project.scripts]` bare keys. |
| `{{project:folder}}`       | The project's folder path (forward slashes)           |
| `{{project:toe_file}}`     | The project's primary `.toe` filename (TD only)       |
| `{{var:VAR_NAME}}`         | The value of a declared `variables:` entry            |

The top-level `{{KEY}}` tokens inside template source files reference
entries in `substitutions:` (not variables directly — one indirection).

## Scaffold command substitution

Inside `scaffold.command`, `{VAR}` placeholders (single braces) are
substituted from the user's collected scaffold input values. After
substitution the string is shlex-split; any `--flag <empty>` pair that
resulted from an empty multiselect is dropped automatically so the
external CLI never sees a stray empty value.

Multiselect values substitute as comma-joined strings
(`"prettier,eslint"`). Booleans substitute as `"1"` / `"0"`.

## How discovery works

On startup `templates.ensure_builtins()` upserts the hardcoded TD
built-ins (`acro-td-core`, `blank`, `example`) and then scans
`resources/templates/<kind>/<name>/` for bundled templates. Each yml
gets loaded with `trusted=True`, so scaffold blocks are honored. The
`Template.toe_path` column stores the template's folder path
(`resources/templates/nodejs/sveltekit/`), which `template_apply.resolve_config`
then unpacks to find `template.yml` inside.

Delete a template by removing its folder; re-run the launcher to drop
the registration.

## Security

User-added templates (Templates view → Add Template...) always load
with `trusted=False`, which silently strips any `scaffold:` block. The
create flow additionally double-checks `Template.builtin` before
invoking the scaffold runner, so there's no way for a user template to
trigger external-CLI execution.

Bundled templates must be reviewed like any other code shipped with
ALPACKA — a malicious `scaffold.command` would run with the user's
permissions at project create time.

## Adding a new template

1. Decide on `<kind>/<name>/`. Create the folder.
2. Write a `template.yml` following the schema above.
3. For file-copy templates, add a `files/` subdir with the source tree.
4. Test via `python -m alpacka` → New Project → your kind → your template.
5. Commit. Discovery picks it up on next launcher startup.

See [`nodejs/sveltekit/template.yml`](nodejs/sveltekit/template.yml) for
a reference scaffold-command template.
