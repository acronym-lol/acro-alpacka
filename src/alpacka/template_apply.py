"""Template config loader + action runner.

Rich templates declare their setup actions in a ``template.yml`` file.
This module:

  1. Resolves the config file for a given `.toe` path
     (:func:`resolve_config`).
  2. Executes the declared actions (:func:`apply`):
     - ``folders:`` — additional folders to mkdir
     - ``files:`` — declarative file copies with optional substitution
     - ``trees:`` — recursive directory copies
     - ``substitutions:`` — values available to files marked
       ``substitute: true``

Substitution source syntax:
  ``{{project:name|slug|folder|toe_file}}`` — reads from the build context
  ``{{var:VAR_NAME}}``            — reads from the project's user-defined
                                    variables (seeded by ``template.yml``'s
                                    ``variables:`` section at creation time)

Unknown substitution sources fail loudly at apply time so typos surface
as errors instead of leaving ``{{...}}`` literals in generated files.

Templates can come from anyone, so every path stays inside its folder:
``folders`` / ``dst`` inside the new project, ``src`` inside the template
(no absolute paths, no ``..``, no symlinks), and nothing reads the
environment.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class TemplateConfigError(RuntimeError):
    """Raised when a template.yml is malformed or references missing files."""


@dataclass
class ApplyContext:
    """Data available for substitution during :func:`apply`."""

    project_name: str
    project_folder: Path
    toe_file: str
    # Per-project variables, chosen at project-creation time from the
    # template's declared `variables:` schema. Used by `{{var:NAME}}`
    # substitution refs inside `substitutions:` values.
    variables: dict[str, Any] = field(default_factory=dict)


@dataclass
class VariableDecl:
    """A declared variable in a template's `variables:` or scaffold `inputs:`.

    Seeds an `alpacka.yml`'s `variables` + `variable_meta` at project-
    creation time (for runtime variables) or feeds scaffold-command argv
    (for scaffold inputs). The ``type`` field controls how the New Project
    dialog renders the input:

      - ``string``/``int``/``float``/``bool``/``path`` — single-line inputs
      - ``enum`` — single-choice dropdown from ``options``
      - ``multiselect`` — multi-choice checkbox panel from ``options``

    ``options`` entries may be plain strings (``"minimal"``) or dicts
    with ``{value, label}`` for human-friendly labels in multi-select UIs.
    """

    name: str
    type: str = "string"
    default: Any = ""
    prompt: str | None = None
    description: str | None = None
    required: bool = False
    # Only meaningful for `enum` and `multiselect`. Normalized to a list of
    # ``{value, label}`` dicts so callers don't have to handle both forms.
    options: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VariableDecl":
        if not isinstance(data, dict) or "name" not in data:
            raise TemplateConfigError(
                f"`variables:` entry must have a `name`: {data!r}"
            )
        raw_options = data.get("options") or []
        options: list[dict[str, str]] = []
        if isinstance(raw_options, list):
            for opt in raw_options:
                if isinstance(opt, dict):
                    value = str(opt.get("value", "") or "")
                    if not value:
                        continue
                    label = str(opt.get("label", value) or value)
                    options.append({"value": value, "label": label})
                else:
                    value = str(opt)
                    if value:
                        options.append({"value": value, "label": value})
        return cls(
            name=str(data["name"]),
            type=str(data.get("type", "string")),
            default=data.get("default", ""),
            prompt=data.get("prompt"),
            description=data.get("description"),
            required=bool(data.get("required", False)),
            options=options,
        )


@dataclass
class FileEntry:
    src: str
    dst: str
    substitute: bool = False
    overwrite: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FileEntry":
        if not isinstance(data, dict) or "src" not in data or "dst" not in data:
            raise TemplateConfigError(
                f"`files:` entry must have `src` and `dst`: {data!r}"
            )
        return cls(
            src=str(data["src"]),
            dst=str(data["dst"]),
            substitute=bool(data.get("substitute", False)),
            overwrite=bool(data.get("overwrite", False)),
        )


@dataclass
class TreeEntry:
    src: str
    dst: str
    overwrite: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TreeEntry":
        if not isinstance(data, dict) or "src" not in data or "dst" not in data:
            raise TemplateConfigError(
                f"`trees:` entry must have `src` and `dst`: {data!r}"
            )
        return cls(
            src=str(data["src"]),
            dst=str(data["dst"]),
            overwrite=bool(data.get("overwrite", False)),
        )


@dataclass
class ScaffoldSpec:
    """A template that scaffolds by running an external CLI.

    Consumed by `template_scaffold.run()` at project-creation time. Only
    honored for **bundled** templates (templates loaded with
    ``trusted=True``) — user-added templates cannot declare a scaffold
    block, which keeps arbitrary command execution off the table for
    untrusted template sources.

    - ``command`` — shell-style template with ``{VAR}`` placeholders.
      Substituted from `inputs` values, then shlex-split. Any
      ``--flag ""`` pair in the resulting argv is dropped so empty
      multi-select values cleanly omit their flag.
    - ``cwd`` — relative to the project folder ALPACKA just created.
    - ``requires_command`` — preflight: `shutil.which` must find this on
      PATH before scaffold runs. If missing, we surface a friendly error.
    - ``inputs`` — scaffold-only variables; prompted in the New Project
      dialog, fed to the command, then discarded (NOT written to
      alpacka.yml).
    """

    command: str
    cwd: str = "."
    requires_command: str = ""
    inputs: list[VariableDecl] = field(default_factory=list)
    # Optional post-scaffold step (e.g. ``npm install``). When present
    # ALPACKA shows an opt-in checkbox in the New Project dialog; on
    # success the install runs in its own progress dialog. Failure is
    # non-fatal — the scaffolded project stays registered.
    post_install: "PostInstallSpec | None" = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScaffoldSpec":
        if not isinstance(data, dict):
            raise TemplateConfigError(
                f"`scaffold:` must be a mapping, got {type(data).__name__}"
            )
        command = str(data.get("command", "") or "")
        if not command:
            raise TemplateConfigError("`scaffold.command` is required")
        raw_inputs = data.get("inputs") or []
        inputs = [
            VariableDecl.from_dict(d)
            for d in raw_inputs
            if isinstance(d, dict)
        ]
        post_install = None
        if data.get("post_install") is not None:
            post_install = PostInstallSpec.from_dict(data["post_install"])
        return cls(
            command=command,
            cwd=str(data.get("cwd", ".") or "."),
            requires_command=str(data.get("requires_command", "") or ""),
            inputs=inputs,
            post_install=post_install,
        )


@dataclass
class PostInstallSpec:
    """Optional dependency-install step that runs after scaffold succeeds."""

    command: str
    cwd: str = "."
    requires_command: str = ""
    prompt: str = "Run install after scaffolding"
    default: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PostInstallSpec":
        if not isinstance(data, dict):
            raise TemplateConfigError(
                f"`scaffold.post_install:` must be a mapping, got {type(data).__name__}"
            )
        command = str(data.get("command", "") or "")
        if not command:
            raise TemplateConfigError("`scaffold.post_install.command` is required")
        return cls(
            command=command,
            cwd=str(data.get("cwd", ".") or "."),
            requires_command=str(data.get("requires_command", "") or ""),
            prompt=str(
                data.get("prompt")
                or "Run install after scaffolding"
            ),
            default=bool(data.get("default", True)),
        )


@dataclass
class TemplateConfig:
    config_path: Path
    schema: int = 1
    name: str | None = None
    description: str | None = None
    toe: str | None = None
    # Which project kind this template applies to. Templates declared
    # before the kind field existed default to touchdesigner.
    kind: str = "touchdesigner"
    folders: list[str] = field(default_factory=list)
    files: list[FileEntry] = field(default_factory=list)
    trees: list[TreeEntry] = field(default_factory=list)
    substitutions: dict[str, str] = field(default_factory=dict)
    # Declared variables — seed `alpacka.yml`'s `variables` at creation.
    variables: list[VariableDecl] = field(default_factory=list)
    # Kind-specific defaults to seed into the new project's alpacka.yml
    # `kind_data.<kind>` section. Lets templates pre-populate fields the
    # kind handler expects (e.g. python's `entry`, executable's `path`)
    # without forcing the user to edit alpacka.yml after create.
    kind_data: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Optional scaffold-by-external-CLI block. None for pure file-copy
    # templates. Parsed only when `_load_config(..., trusted=True)` —
    # dropped for user-added templates.
    scaffold: ScaffoldSpec | None = None

    @property
    def template_dir(self) -> Path:
        return self.config_path.parent


# ---------------------------------------------------------------------------
# Resolution / loading
# ---------------------------------------------------------------------------


def resolve_config(toe_path: Path, *, trusted: bool = False) -> TemplateConfig | None:
    """Find a template.yml for the given `.toe` path, or None.

    Resolution order (first match wins):
      1. If ``toe_path`` is a **directory**: ``toe_path / "template.yml"`` —
         bundled / folder-template form.
      2. ``<toe_path>.parent / "template.yml"`` — TD directory form
         (a ``.toe`` sitting next to a shared template.yml).
      3. ``<toe_path>.parent / (<toe_stem> + ".template.yml")`` —
         sidecar form for user templates with per-.toe configs.
    """
    toe_path = Path(toe_path)

    if toe_path.is_dir():
        direct = toe_path / "template.yml"
        if direct.is_file():
            return _load_config(direct, trusted=trusted)
        return None

    parent = toe_path.parent

    dir_form = parent / "template.yml"
    if dir_form.is_file():
        return _load_config(dir_form, trusted=trusted)

    sidecar = parent / f"{toe_path.stem}.template.yml"
    if sidecar.is_file():
        return _load_config(sidecar, trusted=trusted)

    return None


def _load_config(path: Path, *, trusted: bool = False) -> TemplateConfig:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise TemplateConfigError(f"Failed to parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise TemplateConfigError(f"{path} must parse to a mapping, got {type(data)}")

    # `scaffold:` is only parsed for trusted (bundled) templates. User-
    # added templates that happen to declare a scaffold block get it
    # silently dropped at load time — belt + suspenders with the
    # `builtin=1` check in create.py.
    scaffold: ScaffoldSpec | None = None
    if trusted and data.get("scaffold") is not None:
        scaffold = ScaffoldSpec.from_dict(data["scaffold"])

    cfg = TemplateConfig(
        config_path=path,
        schema=int(data.get("schema", 1)),
        name=data.get("name"),
        description=data.get("description"),
        toe=data.get("toe"),
        kind=str(data.get("kind", "touchdesigner") or "touchdesigner"),
        folders=[str(f) for f in (data.get("folders") or [])],
        files=[FileEntry.from_dict(d) for d in (data.get("files") or [])],
        trees=[TreeEntry.from_dict(d) for d in (data.get("trees") or [])],
        substitutions={k: str(v) for k, v in (data.get("substitutions") or {}).items()},
        variables=[VariableDecl.from_dict(d) for d in (data.get("variables") or [])],
        kind_data=_parse_kind_data(data.get("kind_data") or {}),
        scaffold=scaffold,
    )
    return cfg


def _parse_kind_data(raw: Any) -> dict[str, dict[str, Any]]:
    """Validate the shape of a template.yml's `kind_data:` section.

    Expected: a mapping of kind-name → {field: value, ...}. Anything
    else becomes an empty dict (we'd rather drop malformed data than
    crash the template load).
    """
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for kind_name, fields in raw.items():
        if isinstance(fields, dict):
            result[str(kind_name)] = {str(k): v for k, v in fields.items()}
    return result


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply(
    config: TemplateConfig,
    project_folder: Path,
    context: ApplyContext,
) -> None:
    """Execute all declared actions of a `TemplateConfig`."""
    project_folder = Path(project_folder)
    project_folder.mkdir(parents=True, exist_ok=True)

    _mkdir_folders(config, project_folder)
    resolved_subs = _resolve_substitutions(config.substitutions, context)
    _copy_files(config, project_folder, resolved_subs)
    _copy_trees(config, project_folder)


def _inside(base: Path, rel: str | Path, what: str) -> Path:
    """`base / rel`, refusing anything that ends up outside `base`.

    Templates can come from anyone (a shared folder, a git clone), so a
    template must not be able to write — or read — outside the new
    project folder (or its own folder for sources): no absolute paths, no
    `..`, and no symlinks that point elsewhere.
    """
    base = Path(base).resolve()
    rel_path = Path(rel)
    if rel_path.is_absolute() or rel_path.drive or rel_path.anchor:
        raise TemplateConfigError(f"{what}: absolute paths aren't allowed: {rel}")
    target = (base / rel_path).resolve()
    if target != base and base not in target.parents:
        raise TemplateConfigError(f"{what}: path leaves the folder: {rel}")
    return target


def _no_symlink(path: Path, what: str) -> None:
    if path.is_symlink():
        raise TemplateConfigError(f"{what}: symlinks aren't allowed: {path}")


def _mkdir_folders(config: TemplateConfig, project_folder: Path) -> None:
    for folder in config.folders:
        _inside(project_folder, folder, "folders").mkdir(parents=True, exist_ok=True)


def _copy_files(
    config: TemplateConfig,
    project_folder: Path,
    resolved_subs: dict[str, str],
) -> None:
    for entry in config.files:
        src = _resolve_src(config, entry.src)
        dst = _inside(project_folder, entry.dst, "files: dst")
        if dst.exists() and not entry.overwrite:
            continue
        if not src.is_file():
            raise TemplateConfigError(f"files: src not found: {src}")
        _no_symlink(dst, "files: dst")

        dst.parent.mkdir(parents=True, exist_ok=True)
        if entry.substitute:
            text = src.read_text(encoding="utf-8")
            text = _apply_substitutions(text, resolved_subs)
            dst.write_text(text, encoding="utf-8", newline="\n")
        else:
            shutil.copyfile(src, dst)


def _copy_trees(config: TemplateConfig, project_folder: Path) -> None:
    """Recursively merge each `trees:` entry into the project folder.

    Walks `src` file-by-file and copies into `dst`, skipping individual
    files that already exist unless ``overwrite: true``. This is the
    safe semantic for ``dst: .`` (the project folder, which always
    exists by the time we get here) and lets a tree coexist with
    ``files:`` entries that wrote substituted versions of select files
    earlier in :func:`apply` — those files are kept; the rest of the
    tree fills in around them.
    """
    for entry in config.trees:
        src = _resolve_src(config, entry.src)
        if not src.is_dir():
            raise TemplateConfigError(
                f"trees: src not found or not a dir: {src}"
            )
        dst_root = _inside(project_folder, entry.dst, "trees: dst")
        dst_root.mkdir(parents=True, exist_ok=True)
        for src_file in src.rglob("*"):
            # A symlink inside a template could pull in any file on the
            # machine (an SSH key, a credentials file); skip them.
            if src_file.is_symlink() or not src_file.is_file():
                continue
            if src not in src_file.resolve().parents:
                continue
            dst_file = _inside(dst_root, src_file.relative_to(src), "trees: dst")
            if dst_file.exists() and not entry.overwrite:
                continue
            _no_symlink(dst_file, "trees: dst")
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src_file, dst_file)


def _resolve_src(config: TemplateConfig, rel_path: str) -> Path:
    """Template `src` paths are relative to the config file's dir, and must
    stay inside it (see `_inside`)."""
    _no_symlink(Path(config.template_dir) / rel_path, "src")
    return _inside(config.template_dir, rel_path, "src")


# ---------------------------------------------------------------------------
# Substitutions
# ---------------------------------------------------------------------------

# Matches `{{project:key}}` or `{{var:NAME}}` — the "source" side of the
# mapping values in `substitutions:`. (There used to be an `env:` source;
# it let any template copy any environment variable — tokens included —
# into generated files, and nothing used it, so it was removed.) Not applied directly to target
# files; it resolves the user-defined mapping values first.
_SOURCE_REF = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*:\s*([a-zA-Z0-9_\-]+)\s*\}\}")

# Matches top-level substitution keys inside generated text (e.g.
# `{{ACRO_TD_CORE}}`) — the keys declared in `substitutions:`.
_KEY_REF = re.compile(r"\{\{\s*([A-Z][A-Z0-9_]*)\s*\}\}")


def _resolve_substitutions(
    raw: dict[str, str], context: ApplyContext
) -> dict[str, str]:
    """Resolve the mapping values in `substitutions:` using their sources."""
    resolved: dict[str, str] = {}
    for key, raw_value in raw.items():
        match = _SOURCE_REF.fullmatch(raw_value.strip())
        if match is None:
            # Plain literal value — keep as-is.
            resolved[key] = raw_value
            continue
        source, name = match.group(1), match.group(2)
        resolved[key] = _resolve_source(source, name, context)
    return resolved


def _resolve_source(source: str, name: str, context: ApplyContext) -> str:
    if source == "env":
        raise TemplateConfigError(
            f"substitutions: {{{{env:{name}}}}} isn't supported — templates "
            "can't read environment variables. Use {{var:NAME}} with a "
            "project variable instead."
        )
    if source == "project":
        if name == "name":
            return context.project_name
        if name == "slug":
            # Lower-kebab-case sanitization for places that can't take
            # a free-form name: PEP 621 `[project] name`, npm package
            # name, `[project.scripts]` bare-key entries, etc. — they
            # disallow spaces and most punctuation.
            return _slugify(context.project_name)
        if name == "folder":
            return str(context.project_folder).replace("\\", "/")
        if name == "toe_file":
            return context.toe_file
        raise TemplateConfigError(
            f"substitutions: unknown project attribute '{name}'"
        )
    if source == "var":
        if name not in context.variables:
            raise TemplateConfigError(
                f"substitutions: variable '{name}' not declared in project"
            )
        return str(context.variables[name])
    raise TemplateConfigError(
        f"substitutions: unknown source '{source}' "
        "(expected 'project' or 'var')"
    )


def _apply_substitutions(text: str, resolved: dict[str, str]) -> str:
    """Replace `{{KEY}}` tokens in `text` using the resolved mapping.

    Unknown keys raise rather than leaving the token in place — catches
    typos before they ship to users' generated files.
    """
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in resolved:
            raise TemplateConfigError(
                f"substitutions: token {{{{{key}}}}} used in template file "
                "but not declared in substitutions:"
            )
        return resolved[key]

    return _KEY_REF.sub(repl, text)


_SLUG_REPLACE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    """Reduce a free-form project name to lower-kebab-case.

    "Test Python CLI 2"  →  "test-python-cli-2"
    "My App"             →  "my-app"
    "  weird---name!! "  →  "weird-name"
    ""                   →  "project"  (fallback so callers always get
                                       a syntactically valid identifier)

    The transform is intentionally lossy: any run of non-`[a-z0-9]`
    characters becomes a single dash, and leading/trailing dashes are
    trimmed. Used by `{{project:slug}}` for fields that have parser
    or registry constraints (PEP 621 `[project] name`, npm package
    name, TOML bare keys in `[project.scripts]`).
    """
    lowered = (name or "").strip().lower()
    cleaned = _SLUG_REPLACE.sub("-", lowered).strip("-")
    return cleaned or "project"
