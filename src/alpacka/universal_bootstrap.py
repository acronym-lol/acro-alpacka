"""Minimal per-project setup applied to every newly created project.

Universal bootstrap runs BEFORE template-specific actions and is the
same regardless of which template the user picked. Rich templates can
override individual outputs (most commonly `.gitignore`) via their
`template.yml`.

Scope — deliberately minimal:
  • Create per-kind folders (from the kind handler's `universal_folders`)
  • Copy a baseline ``.gitignore`` from the launcher's bundled resource
  • (``alpacka.yml`` is already written by ``create.create_new``.)

Kind-specific scaffolding lives in the kind handler's `universal_folders`
tuple — TD gets ``scripts/content/tox/TDLogs``; other kinds get their own
or none.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import kinds


def _resource_path(filename: str) -> Path:
    """Resolve a launcher-bundled resource (packaged alongside the module)."""
    return Path(__file__).resolve().parent / "resources" / filename


def apply(
    project_folder: Path,
    *,
    create_folders: bool = True,
    kind: str = "touchdesigner",
) -> None:
    """Run the universal bootstrap against a freshly-created project folder.

    Idempotent — folders are skipped if present, `.gitignore` is only
    written if missing (rich templates that want to override use their
    own `files:` entry with `overwrite: true`).

    `create_folders=False` opts out of the baseline folder set — users
    who want a bare project can skip it. `kind` selects which per-kind
    folder list the handler ships (TD: scripts/content/tox/TDLogs; others
    may ship their own or an empty tuple).
    """
    project_folder = Path(project_folder)
    project_folder.mkdir(parents=True, exist_ok=True)

    if create_folders:
        handler = kinds.get(kind)
        for folder in handler.universal_folders:
            (project_folder / folder).mkdir(parents=True, exist_ok=True)

    gitignore_src = _resource_path("universal.gitignore")
    gitignore_dst = project_folder / ".gitignore"
    if gitignore_src.is_file() and not gitignore_dst.exists():
        shutil.copyfile(gitignore_src, gitignore_dst)
