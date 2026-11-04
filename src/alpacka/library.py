"""Project library — SQLite registry + on-disk alpacka.yml merge.

The DB stores only the stuff the launcher alone needs (path, pin status,
last-launched timestamp, cached stale flag, kind). Everything else comes
from the alpacka.yml file inside each project folder.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import db, process_tracker, project, tags


@dataclass
class LibraryEntry:
    row_id: int
    path: Path
    added_at: str
    pinned: bool
    last_launched: str
    stale: bool
    project: project.Project | None  # None if alpacka.yml missing/unreadable
    # All running TD processes for this project (may be >1 for multi-toe
    # launches or "Launch new instance"). Populated by list_entries / get.
    running_processes: list[process_tracker.ProcessRecord] = field(
        default_factory=list
    )
    # Fingerprint of the run settings the user approved on this machine
    # (see approval.py). "" = never approved.
    approved_run_hash: str = ""


def add(path: Path) -> int:
    """Register a project folder. Returns the new row ID.

    Raises ValueError if the path is already registered.
    """
    path = Path(path).resolve()
    with db.connection() as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO projects (path, added_at, pinned, last_launched, stale) "
                "VALUES (?, ?, 0, NULL, 0)",
                (str(path), db.now_iso()),
            )
        except Exception as exc:  # noqa: BLE001 - sqlite3 IntegrityError
            raise ValueError(f"Project already in library: {path}") from exc
        return cursor.lastrowid


def remove(row_id: int) -> None:
    """Remove a project from the library. Disk state is never touched."""
    with db.connection() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (row_id,))


def get(row_id: int) -> LibraryEntry | None:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (row_id,)
        ).fetchone()
    return _row_to_entry(row) if row else None


def list_entries() -> list[LibraryEntry]:
    """Return all library entries, pinned first, then most recent.

    Each entry is populated with any running TD processes tracked for
    that project in the same DB so the UI can render running state
    without a second round-trip.
    """
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM projects "
            "ORDER BY pinned DESC, "
            "COALESCE(last_launched, added_at) DESC"
        ).fetchall()
    entries = [_row_to_entry(r) for r in rows]

    # One pass of all running_processes rows, then group by project_id.
    by_project: dict[int, list[process_tracker.ProcessRecord]] = {}
    for rec in process_tracker.list_all():
        by_project.setdefault(rec.project_id, []).append(rec)
    for entry in entries:
        entry.running_processes = by_project.get(entry.row_id, [])
    return entries


def mark_launched(row_id: int) -> None:
    with db.connection() as conn:
        conn.execute(
            "UPDATE projects SET last_launched = ?, stale = 0 WHERE id = ?",
            (db.now_iso(), row_id),
        )


def set_approved_run_hash(row_id: int, value: str | None) -> None:
    with db.connection() as conn:
        conn.execute(
            "UPDATE projects SET approved_run_hash = ? WHERE id = ?",
            (value, row_id),
        )


def set_pinned(row_id: int, pinned: bool) -> None:
    with db.connection() as conn:
        conn.execute(
            "UPDATE projects SET pinned = ? WHERE id = ?",
            (1 if pinned else 0, row_id),
        )


def refresh_stale() -> int:
    """Recheck every project's folder + .toe existence. Returns stale count.

    Also sweeps the process tracker so dead TD PIDs are purged on every
    library refresh — keeps the card running-state honest without
    needing a separate periodic caller. Any tag strings referenced by
    projects but missing from `tags.json` get auto-added as roots so
    the sidebar tree always reflects what's actually in use.
    """
    process_tracker.sweep_dead()

    stale_count = 0
    seen_tags: set[str] = set()
    with db.connection() as conn:
        rows = conn.execute("SELECT id, path FROM projects").fetchall()
        for row in rows:
            p = project.load(Path(row["path"]))
            is_stale = p is None or p.is_stale
            conn.execute(
                "UPDATE projects SET stale = ? WHERE id = ?",
                (1 if is_stale else 0, row["id"]),
            )
            if is_stale:
                stale_count += 1
            if p is not None:
                seen_tags.update(p.tags)

    if seen_tags:
        tree = tags.load()
        # Sort so the first-migration order of root nodes is stable
        # regardless of project-iteration order.
        if tags.ensure_tags_exist(tree, sorted(seen_tags)):
            tags.save(tree)
    return stale_count


def _row_to_entry(row) -> LibraryEntry:
    path = Path(row["path"])
    return LibraryEntry(
        row_id=row["id"],
        path=path,
        added_at=row["added_at"],
        pinned=bool(row["pinned"]),
        last_launched=row["last_launched"] or "",
        stale=bool(row["stale"]),
        project=project.load(path),
        running_processes=process_tracker.list_for_project(row["id"]),
        approved_run_hash=(
            (row["approved_run_hash"] or "") if "approved_run_hash" in row.keys() else ""
        ),
    )
