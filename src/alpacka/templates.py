"""Template CRUD + built-in template seeding.

Built-in templates are hardcoded entries re-resolved on every launcher
startup so their `toe_path` tracks the current environment:

- `acro-td-core` → `$ACRO_TD_CORE/<toe from template.yml>` (the `.toe`
  filename is read from the repo's `template.yml`; falls back to
  `AcroCore.toe` if that file is missing)
- `blank` → `<TD install>/Samples/Setup/Base/NewProject.toe` (TD ships
  a clean "New Project" scene in its Samples folder; we reuse it instead
  of asking the user to generate their own blank .toe).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from . import db, paths, td_install

BUILTIN_ACRO_TD_CORE = "acro-td-core"
BUILTIN_BLANK = "blank"
BUILTIN_EXAMPLE = "example"

# (name, description). Resolution is handled per-name in _resolve_builtin_path.
BUILTIN_SPECS: list[tuple[str, str]] = [
    (
        BUILTIN_ACRO_TD_CORE,
        "Acro TD Core — AcroCore framework with version management, MCP, hotkeys, and widget tooling.",
    ),
    (
        BUILTIN_BLANK,
        "Blank — TouchDesigner's bundled New Project scene.",
    ),
    (
        BUILTIN_EXAMPLE,
        "Example — TouchDesigner's bundled Example scene (includes a default rendering setup).",
    ),
]


@dataclass
class Template:
    id: int
    name: str
    description: str
    toe_path: str
    builtin: bool
    added_at: str
    kind: str = "touchdesigner"

    @property
    def source_exists(self) -> bool:
        # TD templates require a valid `.toe` file on disk. Other kinds can
        # point at either a folder (bundled templates, user-added folder
        # templates) or a file (a single .yml / .sh / etc.) — or nothing at
        # all for a sourceless kind declaration.
        if self.kind == "touchdesigner":
            return bool(self.toe_path) and Path(self.toe_path).is_file()
        if not self.toe_path:
            return True  # source-less template is valid for non-TD kinds
        p = Path(self.toe_path)
        return p.is_file() or p.is_dir()


# Fallback `.toe` filename for the acro-td-core built-in, used only when the
# repo's template.yml is missing or omits a `toe:` field. Normally the filename
# is read from `$ACRO_TD_CORE/template.yml`, so a rename on the acro-td-core
# side is tracked automatically without a launcher change.
_ACRO_TD_CORE_DEFAULT_TOE = "AcroCore.toe"


def _resolve_acro_td_core_path() -> str:
    root = paths.acro_td_core_dir()
    if root is None:
        return ""
    # Deferred import: template_apply pulls in YAML libs that DB-only callers
    # don't need at module load time.
    from . import template_apply

    toe_name = _ACRO_TD_CORE_DEFAULT_TOE
    try:
        cfg = template_apply.resolve_config(root)
        if cfg is not None and cfg.toe:
            toe_name = cfg.toe
    except template_apply.TemplateConfigError:
        pass  # malformed yml — fall back to the default filename
    return str(root / toe_name)


def _resolve_td_sample_path(*parts: str) -> str:
    """Resolve a path under `<TD install>/Samples/` for the newest TD install."""
    installs = td_install.detect()
    install = td_install.newest(installs)
    if install is None:
        return ""

    if sys.platform == "win32":
        # Install dir is two levels above the .exe (bin/TouchDesigner.exe).
        install_dir = Path(install.path).parent.parent
        candidate = install_dir.joinpath("Samples", *parts)
    elif sys.platform == "darwin":
        # TD's macOS .app bundle nests Samples inside the `tfs/` directory
        # under Contents/Resources — i.e. /Applications/TouchDesigner.app
        # /Contents/Resources/tfs/Samples/Setup/Base/NewProject.toe.
        candidate = Path(install.path).joinpath(
            "Contents", "Resources", "tfs", "Samples", *parts
        )
    else:
        return ""

    return str(candidate) if candidate.is_file() else ""


def _resolve_builtin_path(name: str) -> str:
    if name == BUILTIN_ACRO_TD_CORE:
        return _resolve_acro_td_core_path()
    if name == BUILTIN_BLANK:
        return _resolve_td_sample_path("Setup", "Base", "NewProject.toe")
    if name == BUILTIN_EXAMPLE:
        return _resolve_td_sample_path("Setup", "Example", "NewProject.toe")
    return ""


def ensure_builtins() -> None:
    """Upsert built-in templates — hardcoded TD ones + bundled discovery.

    Safe to call on every startup. Bundled templates are discovered from
    `src/alpacka/resources/templates/<kind>/<name>/` and upserted with
    `builtin=1` alongside the TD externals (AcroCore, blank, example).
    """
    with db.connection() as conn:
        for name, description in BUILTIN_SPECS:
            toe_path = _resolve_builtin_path(name)
            _upsert_builtin(conn, name, description, toe_path, kind="touchdesigner")

        for spec in _discover_bundled():
            _upsert_builtin(
                conn, spec.name, spec.description, spec.source_path, kind=spec.kind
            )


def _upsert_builtin(
    conn, name: str, description: str, source_path: str, *, kind: str
) -> None:
    existing = conn.execute(
        "SELECT id FROM templates WHERE name = ?", (name,)
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO templates (name, description, toe_path, builtin, added_at, kind) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (name, description, source_path, db.now_iso(), kind),
        )
    else:
        conn.execute(
            "UPDATE templates SET description = ?, toe_path = ?, builtin = 1, kind = ? "
            "WHERE name = ?",
            (description, source_path, kind, name),
        )


@dataclass
class _BundledSpec:
    name: str
    description: str
    kind: str
    source_path: str  # absolute path to the template folder


def _bundled_templates_root() -> Path:
    return Path(__file__).resolve().parent / "resources" / "templates"


def _discover_bundled() -> list[_BundledSpec]:
    """Scan resources/templates/<kind>/<name>/ and return discovered templates.

    Each template folder must contain a ``template.yml``. The yml's ``name``
    and ``description`` fields win over the folder name when present; ``kind``
    defaults to the parent folder's name (e.g. ``nodejs``) if the yml omits it.
    """
    root = _bundled_templates_root()
    if not root.is_dir():
        return []

    # Deferred import — template_apply imports YAML libs that aren't needed
    # at module load time for tools that only touch the DB.
    from . import template_apply

    discovered: list[_BundledSpec] = []
    for kind_dir in sorted(root.iterdir()):
        if not kind_dir.is_dir() or kind_dir.name.startswith("."):
            continue
        for tmpl_dir in sorted(kind_dir.iterdir()):
            if not tmpl_dir.is_dir() or tmpl_dir.name.startswith("."):
                continue
            yml_path = tmpl_dir / "template.yml"
            if not yml_path.is_file():
                continue
            try:
                # Bundled templates are trusted — scaffold blocks are
                # honored. User-added templates load without `trusted=True`
                # and silently drop any scaffold block they happen to carry.
                cfg = template_apply._load_config(yml_path, trusted=True)
            except template_apply.TemplateConfigError:
                # Bad yml — skip; surface on the Templates view as missing source
                continue
            name = str(cfg.name or tmpl_dir.name)
            description = str(cfg.description or "")
            kind = str(cfg.kind or kind_dir.name)
            discovered.append(
                _BundledSpec(
                    name=name,
                    description=description,
                    kind=kind,
                    source_path=str(tmpl_dir),
                )
            )
    return discovered


def list_all(kind: str | None = None) -> list[Template]:
    """Return all templates (optionally filtered by kind), built-ins first."""
    with db.connection() as conn:
        if kind is None:
            rows = conn.execute(
                "SELECT * FROM templates ORDER BY builtin DESC, name ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM templates WHERE kind = ? "
                "ORDER BY builtin DESC, name ASC",
                (kind,),
            ).fetchall()
    return [_row_to_template(r) for r in rows]


def get_by_name(name: str) -> Template | None:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM templates WHERE name = ?", (name,)
        ).fetchone()
    return _row_to_template(row) if row else None


def add_user_template(
    name: str,
    description: str,
    toe_path: str,
    *,
    kind: str = "touchdesigner",
) -> Template:
    """Insert a new user-added template. Raises ValueError on duplicate name.

    ``kind`` drives how the template is consumed at create time: TD
    templates point at a ``.toe`` file; non-TD templates point at a
    folder (or nothing at all, for a bare kind declaration).
    """
    with db.connection() as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO templates (name, description, toe_path, builtin, added_at, kind) "
                "VALUES (?, ?, ?, 0, ?, ?)",
                (name, description, toe_path, db.now_iso(), kind),
            )
        except Exception as exc:  # noqa: BLE001 - sqlite3.IntegrityError wrapped
            raise ValueError(f"Template name already exists: {name}") from exc
        new_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM templates WHERE id = ?", (new_id,)).fetchone()
    assert row is not None
    return _row_to_template(row)


def update_user_template(
    template_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    toe_path: str | None = None,
    kind: str | None = None,
) -> None:
    """Update a user template. Built-ins are not editable — raises ValueError."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT builtin FROM templates WHERE id = ?", (template_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Template id {template_id} not found")
        if row["builtin"]:
            raise ValueError("Built-in templates cannot be edited")
        fields = []
        values: list[object] = []
        if name is not None:
            fields.append("name = ?")
            values.append(name)
        if description is not None:
            fields.append("description = ?")
            values.append(description)
        if toe_path is not None:
            fields.append("toe_path = ?")
            values.append(toe_path)
        if kind is not None:
            fields.append("kind = ?")
            values.append(kind)
        if not fields:
            return
        values.append(template_id)
        conn.execute(f"UPDATE templates SET {', '.join(fields)} WHERE id = ?", values)


def delete_user_template(template_id: int) -> None:
    """Delete a user template. Built-ins are not deletable — raises ValueError."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT builtin FROM templates WHERE id = ?", (template_id,)
        ).fetchone()
        if row is None:
            return
        if row["builtin"]:
            raise ValueError("Built-in templates cannot be deleted")
        conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))


def _row_to_template(row) -> Template:
    try:
        kind = row["kind"]
    except (IndexError, KeyError):
        kind = "touchdesigner"
    return Template(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        toe_path=row["toe_path"],
        builtin=bool(row["builtin"]),
        added_at=row["added_at"],
        kind=kind or "touchdesigner",
    )
