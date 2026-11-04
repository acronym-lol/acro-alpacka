"""Import an existing project folder into the library.

For TouchDesigner projects, scan for candidate `.toe` files, extract
version info via `toeexpand`, and write an `alpacka.yml`. For other
kinds (nodejs, python, etc.), skip the `.toe` scan and write a minimal
`alpacka.yml` with the kind pre-populated; the user fills in the rest
from Edit Details.

Auto-detection picks the most likely kind per
`kinds.detect_kind(folder)`; the dialog surfaces signals and lets the
user override.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import approval, kinds, library, project, toe_inspect

# Matches TD's auto-numbered backups: `Something.246.toe`, `Something.1.1.0.toe`, etc.
_BACKUP_SUFFIX = re.compile(r"\.[\d][\d.]*\.toe$", re.IGNORECASE)
_BACKUP_DIR_NAMES = {"Backup", "backup"}


class AddExistingError(RuntimeError):
    pass


@dataclass
class ScanResult:
    folder: Path
    candidate_toes: list[Path]  # relative to folder


def scan_folder(folder: Path) -> ScanResult:
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise AddExistingError(f"Folder does not exist: {folder}")

    candidates: list[Path] = []
    for entry in sorted(folder.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix.lower() != ".toe":
            continue
        if _BACKUP_SUFFIX.search(entry.name):
            continue
        candidates.append(entry.relative_to(folder))

    return ScanResult(folder=folder, candidate_toes=candidates)


def add_existing(
    folder: Path,
    toe_file: str | None = None,
    *,
    tags: list[str] | None = None,
    kind: str = "touchdesigner",
) -> int:
    """Import an existing folder. Returns the new library row ID.

    `kind` picks the import flow — for touchdesigner we scan for `.toe`
    files and extract version info; for other kinds we skip the scan and
    write a minimal alpacka.yml with the kind pre-populated.

    `toe_file` is the filename of the primary .toe (relative to `folder`),
    only meaningful for touchdesigner. `tags` always overwrites existing
    tags on the project.
    """
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise AddExistingError(f"Folder does not exist: {folder}")

    tag_list = list(tags or [])

    if kind == "touchdesigner":
        wrote_new = _import_touchdesigner(folder, toe_file, tag_list)
    else:
        wrote_new = _import_other_kind(folder, kind, tag_list)

    try:
        row_id = library.add(folder)
    except ValueError as exc:
        raise AddExistingError(str(exc)) from exc
    # An alpacka.yml ALPACKA just wrote runs nothing unexpected. One that
    # came with the folder (a clone, a shared drive) is reviewed before
    # it first runs — see approval.py.
    if wrote_new:
        approval.approve_row(row_id)
    return row_id


def _import_touchdesigner(
    folder: Path, toe_file: str | None, tag_list: list[str]
) -> bool:
    """Returns True if it wrote a new alpacka.yml."""
    scan = scan_folder(folder)
    if not scan.candidate_toes:
        raise AddExistingError(
            f"No .toe file found in {scan.folder} (auto-numbered backups are ignored)."
        )

    candidate_names = [str(p) for p in scan.candidate_toes]
    if toe_file:
        if toe_file not in candidate_names:
            raise AddExistingError(
                f"Primary .toe '{toe_file}' is not among the scanned candidates: "
                f"{', '.join(candidate_names)}"
            )
        primary = toe_file
    else:
        primary = candidate_names[0]

    alternates = [name for name in candidate_names if name != primary]

    full_toe = scan.folder / primary
    if not full_toe.is_file():
        raise AddExistingError(f".toe not found at {full_toe}")

    existing = project.load(scan.folder)
    if existing is None:
        try:
            info = toe_inspect.read(full_toe)
        except toe_inspect.ToeInspectError as exc:
            raise AddExistingError(str(exc)) from exc
        project.bootstrap_from_existing(
            scan.folder,
            toe_file=primary,
            td_version=info.build,
            td_major=info.version,
            alternate_toe_files=alternates,
            tags=tag_list,
        )
        return True
    else:
        # Merge discovered alternates into existing alpacka.yml without
        # clobbering user-chosen ordering. Skip anything that's already listed
        # (including the primary).
        known = {existing.toe_file, *existing.alternate_toe_files}
        new_alts = [a for a in alternates if a not in known]
        dirty = False
        if new_alts:
            existing.alternate_toe_files = list(existing.alternate_toe_files) + new_alts
            dirty = True
        # Overwrite tags with whatever the dialog supplied — it preloads
        # existing tags on folder-pick, so this only differs from the
        # current state when the user actually edited them.
        if list(existing.tags) != tag_list:
            existing.tags = tag_list
            dirty = True
        if dirty:
            project.save(existing)
        return False


def _import_other_kind(folder: Path, kind: str, tag_list: list[str]) -> bool:
    """Import a non-TD project: write a minimal alpacka.yml if missing.

    Further kind-specific setup (entry point, run configs, variables)
    happens via Edit Details after the import completes.
    """
    # Validate the kind is known.
    _ = kinds.get(kind)

    existing = project.load(folder)
    if existing is None:
        from .project import Project

        proj = Project(
            folder=folder,
            name=folder.name,
            kind=kind,
            created=project._now(),
            last_opened=project._now(),
            tags=list(tag_list),
        )
        project.save(proj)
        return True
    else:
        dirty = False
        if existing.kind != kind:
            existing.kind = kind
            dirty = True
        if list(existing.tags) != tag_list:
            existing.tags = tag_list
            dirty = True
        if dirty:
            project.save(existing)
        return False
