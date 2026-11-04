"""Track processes the launcher has spawned.

Each launch registers a row in `running_processes` with the project's
kind. A periodic sweep (plus every library refresh) asks the kind handler
whether each row is still alive — long-running kinds check pid + process
name pattern (defense against OS PID reuse), one-shot kinds check exit
status.

Platform helpers (`focus`, `terminate`) also dispatch through the kind
handler so each kind brings its own behavior (TD's AppleScript on macOS,
SW_RESTORE-guarded Windows focus, wmctrl on Linux).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

import psutil

from . import db, kinds


@dataclass
class ProcessRecord:
    id: int
    project_id: int
    pid: int
    toe_file: str
    td_version: str
    started_at: str
    kind: str = "touchdesigner"
    process_name_pattern: str = "touchdesigner"
    run_config_name: str = "default"
    # How the launch was wired for stdio capture: "console" (external
    # terminal, no buffer), "inline" (piped + buffer in output_manager),
    # or "none" (silent — current legacy behavior). v4 schema column.
    output_mode: str = "none"
    exit_code: int | None = None
    completed_at: str = ""
    # v5 — dependency lifecycle flags. Only meaningful when kind="dependency".
    # `keep_alive_after_main`: don't kill this dep when the main process for
    # the project dies. `oneshot`: don't auto-kill on stop because the dep
    # exits on its own; mark_completed handles its lifecycle.
    keep_alive_after_main: bool = False
    oneshot: bool = False
    # v8 — the OS process start time (psutil `create_time`) captured at
    # launch. Identifies the exact process, so a PID the OS later reuses
    # for another program is never mistaken for ours (focus, kill).
    create_time: float | None = None


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------


def register(
    project_id: int,
    pid: int,
    *,
    toe_file: str = "",
    td_version: str = "",
    kind: str = "touchdesigner",
    process_name_pattern: str = "touchdesigner",
    run_config_name: str = "default",
    output_mode: str = "none",
    keep_alive_after_main: bool = False,
    oneshot: bool = False,
) -> ProcessRecord:
    """Record a newly-spawned process.

    `kind` / `process_name_pattern` / `run_config_name` were added in
    schema v3; `output_mode` arrived in v4. `keep_alive_after_main` and
    `oneshot` arrived in v5 for launch dependencies — they let the crash
    monitor decide whether to kill a dep on main exit and let sweep_dead
    treat one-shot deps the same as one-shot main processes.
    Duplicate PIDs upsert defensively. The process start time is stored
    (v8) so later kills can check the PID still belongs to this process.
    """
    try:
        created: float | None = psutil.Process(pid).create_time()
    except (psutil.Error, OSError):
        created = None
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO running_processes "
            "(project_id, pid, toe_file, td_version, started_at, "
            "kind, process_name_pattern, run_config_name, output_mode, "
            "keep_alive_after_main, oneshot, create_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(pid) DO UPDATE SET "
            "project_id = excluded.project_id, "
            "toe_file = excluded.toe_file, "
            "td_version = excluded.td_version, "
            "started_at = excluded.started_at, "
            "kind = excluded.kind, "
            "process_name_pattern = excluded.process_name_pattern, "
            "run_config_name = excluded.run_config_name, "
            "output_mode = excluded.output_mode, "
            "keep_alive_after_main = excluded.keep_alive_after_main, "
            "oneshot = excluded.oneshot, "
            "create_time = excluded.create_time, "
            "exit_code = NULL, completed_at = NULL",
            (
                project_id,
                pid,
                toe_file,
                td_version,
                db.now_iso(),
                kind,
                process_name_pattern,
                run_config_name,
                output_mode,
                int(bool(keep_alive_after_main)),
                int(bool(oneshot)),
                created,
            ),
        )
        row = conn.execute(
            "SELECT * FROM running_processes WHERE pid = ?", (pid,)
        ).fetchone()
    return _row_to_record(row)


def list_for_project(project_id: int) -> list[ProcessRecord]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM running_processes WHERE project_id = ? "
            "ORDER BY started_at DESC",
            (project_id,),
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def list_all() -> list[ProcessRecord]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM running_processes ORDER BY started_at DESC"
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def get_by_pid(pid: int) -> Optional[ProcessRecord]:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM running_processes WHERE pid = ?", (pid,)
        ).fetchone()
    return _row_to_record(row) if row is not None else None


def remove(pid: int) -> None:
    with db.connection() as conn:
        conn.execute("DELETE FROM running_processes WHERE pid = ?", (pid,))


def mark_completed(pid: int, exit_code: int | None) -> None:
    """Record that a one-shot process finished.

    Sets `exit_code` + `completed_at` but leaves the row in place so the
    UI can surface the result (e.g. "exit 0") for the rest of the session.
    A completed row is never running, focused or killed; `clear_completed`
    drops them at startup.
    """
    with db.connection() as conn:
        conn.execute(
            "UPDATE running_processes SET exit_code = ?, completed_at = ? "
            "WHERE pid = ?",
            (exit_code, db.now_iso(), pid),
        )


def _row_to_record(row) -> ProcessRecord:
    return ProcessRecord(
        id=row["id"],
        project_id=row["project_id"],
        pid=row["pid"],
        toe_file=row["toe_file"] or "",
        td_version=row["td_version"] or "",
        started_at=row["started_at"],
        kind=(_col(row, "kind") or "touchdesigner"),
        # "" is a real value (generic / shell kinds: no name convention);
        # only a missing column (pre-v3) means the old TD default.
        process_name_pattern=_pattern_or_default(_col(row, "process_name_pattern")),
        run_config_name=(_col(row, "run_config_name") or "default"),
        output_mode=(_col(row, "output_mode") or "none"),
        exit_code=_col(row, "exit_code"),
        completed_at=(_col(row, "completed_at") or ""),
        keep_alive_after_main=bool(_col(row, "keep_alive_after_main") or 0),
        oneshot=bool(_col(row, "oneshot") or 0),
        create_time=_col(row, "create_time"),
    )


def _pattern_or_default(value) -> str:
    return "touchdesigner" if value is None else str(value)


def _col(row, name: str):
    """Safe access to a sqlite row column, returning None if absent."""
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Liveness — dispatches to the kind handler
# ---------------------------------------------------------------------------


def is_alive(pid: int) -> bool:
    """True iff `pid` is still considered alive by its kind handler.

    Returns False if `pid` isn't in the `running_processes` table (nothing
    to check against) or if the kind handler says so. Long-running kinds
    check pid + process-name substring; one-shot kinds check exit status.
    """
    rec = get_by_pid(pid)
    if rec is None:
        return False
    handler = kinds.get(rec.kind)
    try:
        return handler.liveness(rec)
    except Exception:  # noqa: BLE001 — defensive: a buggy handler shouldn't wedge the sweep
        return False


def sweep_dead() -> list[ProcessRecord]:
    """Remove rows whose process is no longer alive per its kind handler.

    Returns the removed records so crash_monitor can decide whether to
    auto-relaunch. Dispatches per kind.

    One-shot rows with `completed_at` set are NOT removed — they're kept
    so the UI can surface the exit result (e.g. "exit 0") until the next
    startup (`clear_completed`).
    """
    dead: list[ProcessRecord] = []
    for rec in list_all():
        if rec.completed_at:
            # Already marked complete by the one-shot watcher; leave
            # the row in place for UI display.
            continue
        handler = kinds.get(rec.kind)
        try:
            alive = handler.liveness(rec)
        except Exception:  # noqa: BLE001
            alive = False
        if not alive:
            dead.append(rec)
            remove(rec.pid)
    return dead


def clear_completed() -> int:
    """Drop finished one-shot rows (run at startup). Returns how many."""
    with db.connection() as conn:
        cur = conn.execute(
            "DELETE FROM running_processes WHERE completed_at IS NOT NULL"
        )
        return cur.rowcount


def active(records: list[ProcessRecord]) -> list[ProcessRecord]:
    """Records still running — finished one-shot rows left out."""
    return [r for r in records if not r.completed_at]


def any_td_running() -> bool:
    """True if any TouchDesigner process is running, tracked or not.

    Used before writing TD's machine-wide `pref.txt`: every running TD
    shares that file and rewrites all of it from memory when its
    Preferences dialog closes, including instances ALPACKA didn't start.
    Same name match as TD liveness (docs/process-tracking.md).
    """
    pattern = kinds.get("touchdesigner").default_process_name_pattern.lower()
    names = _process_names_windows() if sys.platform == "win32" else None
    if names is not None:
        return any(pattern in n.lower() for n in names)
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if pattern in name:
            return True
    return False


def _process_names_windows() -> list[str] | None:
    """Exe names of every process, from one Toolhelp32 snapshot.

    psutil's cold `process_iter(["name"])` takes seconds on Windows: for
    each process it can't open, `name()` falls back to a lookup that
    enumerates every process again. One snapshot lists all exe names at
    once (milliseconds). Returns None on failure so callers fall back.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    TH32CS_SNAPPROCESS = 0x00000002
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return None
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        names: list[str] = []
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            names.append(entry.szExeFile)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        return names or None
    except OSError:
        return None
    finally:
        kernel32.CloseHandle(snapshot)


def is_process_frozen(pid: int) -> bool | None:
    """Is `pid`'s main window unresponsive?

    - ``True``: at least one top-level visible titled window owned by
      the pid is hung (failing to pump its message queue).
    - ``False``: we found at least one such window and none are hung.
    - ``None``: the platform does not support hang detection (non-Windows)
      or no suitable window is available yet.

    Windows-only in v1 via ``user32!IsHungAppWindow``. Generic to any
    windowed kind — TD, Node Electron, etc. — so kept at this level.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None

    user32 = ctypes.windll.user32
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
    )
    any_hung = [False]
    found_any = [False]

    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetParent(hwnd) != 0:
            return True
        pid_out = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_out))
        if pid_out.value != pid:
            return True
        if user32.GetWindowTextLengthW(hwnd) <= 0:
            return True
        found_any[0] = True
        if user32.IsHungAppWindow(hwnd):
            any_hung[0] = True
            return False
        return True

    try:
        user32.EnumWindows(EnumWindowsProc(_callback), 0)
    except OSError:
        return None
    if not found_any[0]:
        return None
    return any_hung[0]


# ---------------------------------------------------------------------------
# Platform helpers — focus + terminate (dispatched through kind handlers)
# ---------------------------------------------------------------------------


def focus_window(pid: int) -> bool:
    """Bring the process's window to the foreground. True on success.

    Dispatches through the kind handler so each kind brings its own
    platform strategy (TD's AppleScript on mac, wmctrl on Linux, etc.).
    """
    rec = get_by_pid(pid)
    if rec is None:
        return False
    handler = kinds.get(rec.kind)
    if not handler.liveness(rec):
        return False
    try:
        return handler.focus(rec)
    except Exception:  # noqa: BLE001
        return False


def terminate_process(pid: int, timeout: float = 5.0) -> bool:
    """Graceful terminate then force-kill on timeout. Cleans up DB row.

    Dispatches through the kind handler (TD uses psutil; others may need
    different signal semantics — `docker compose down`, `npm kill`, etc.).
    """
    rec = get_by_pid(pid)
    if rec is None:
        # No record means nothing proves this PID is ours — it may already
        # belong to another program. Never kill it.
        return not psutil.pid_exists(pid)
    if rec.completed_at:
        # A finished one-shot: its PID may have been reused. Just forget it.
        remove(pid)
        return True

    handler = kinds.get(rec.kind)
    try:
        ok = handler.terminate(rec, timeout)
    except Exception:  # noqa: BLE001
        ok = False
    remove(pid)
    return ok


def terminate_project_processes(project_id: int, timeout: float = 5.0) -> int:
    """Terminate every tracked process for a project. Returns count terminated.

    Dependencies are killed first so a dep that talks to the main process
    (a Python worker subscribed to TD's UDP feed, an OSC bridge, etc.)
    doesn't spam errors during the brief window where main is gone but
    the dep is still draining its queue.
    """
    records = list_for_project(project_id)
    for rec in records:
        if rec.completed_at:
            remove(rec.pid)  # finished one-shot: nothing to stop
    records = active(records)
    deps = [r for r in records if r.kind == "dependency"]
    others = [r for r in records if r.kind != "dependency"]
    n = 0
    for rec in deps + others:
        if terminate_process(rec.pid, timeout):
            n += 1
    return n
