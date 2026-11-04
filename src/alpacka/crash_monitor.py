"""Auto-relaunch monitored projects on crash or freeze.

A project can opt into monitoring via `reopen_on_crash` (configured from
the Autostart Settings dialog on each card). When the monitored process

- **dies** (clean crash / killed externally) — detected by the library
  view's 6-second sweep of `running_processes`; or
- **hangs** (main window unresponsive for `freeze_timeout_seconds`, Windows
  only) — detected here via `process_tracker.is_process_frozen`; we
  force-kill it and treat it the same as a crash,

the launcher schedules a relaunch after `relaunch_delay_seconds` using
the same `launch.launch_toe` path a manual launch uses.

State is kept in module-level dicts and resets when the app restarts —
losing a retry counter across restarts is acceptable and avoids a DB
migration.

Called from `library_view._poll_running_processes` once per 6s tick.
"""
from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import QTimer

from . import approval, launch, library, process_tracker
from .process_tracker import ProcessRecord


# row_id → consecutive auto-relaunches since the last manual launch.
_retry_counts: dict[int, int] = {}

# pid → timestamp (time.time()) when the hang-detector first saw this
# pid as unresponsive. Cleared when the window becomes responsive again
# or the process dies.
_hang_first_seen: dict[int, float] = {}

# row_ids with a QTimer.singleShot armed — guards against scheduling
# two relaunches if a freeze-kill and a clean-crash both fire in the
# same tick.
_pending_relaunch: set[int] = set()

# row_ids where the user just clicked Stop — the next dead-process
# detection for that row consumes the flag and skips relaunch.
_suppress_next: set[int] = set()


def reset(row_id: int) -> None:
    """Clear monitoring state for a project — call on manual launch.

    The user pressing Launch is a fresh slate: retry counter goes back
    to zero, any pending relaunch is consumed, any hang tracking on old
    pids is dropped.
    """
    _retry_counts.pop(row_id, None)
    _pending_relaunch.discard(row_id)
    _suppress_next.discard(row_id)
    # Hang tracking is keyed by pid so old entries time themselves out
    # naturally, but clear them proactively for the project's current
    # live pids so a stale "first seen" doesn't trip on the next tick.
    try:
        for rec in process_tracker.list_for_project(row_id):
            _hang_first_seen.pop(rec.pid, None)
    except Exception:  # noqa: BLE001 — DB hiccups shouldn't crash launch
        pass


def suppress_next_relaunch(row_id: int) -> None:
    """Skip the next auto-relaunch for a project — call on Stop.

    When the user deliberately terminates a process, we don't want the
    crash monitor to immediately spawn it again. One flag covers the
    single impending dead-process detection; subsequent crashes resume
    normal monitoring.
    """
    _suppress_next.add(row_id)


def tick(
    dead: list[ProcessRecord], alive: list[ProcessRecord]
) -> None:
    """Advance the crash monitor by one poll cycle.

    `dead` is the list just returned from `process_tracker.sweep_dead()`
    — rows that were alive on the last tick and are now gone. `alive`
    is the current set of registered-and-live processes.

    Both passes are best-effort: any exception is swallowed so a single
    bad record doesn't stop the launcher's UI from refreshing.
    """
    # Pass 1 — handle crashed processes.
    for record in dead:
        try:
            _hang_first_seen.pop(record.pid, None)
            if record.project_id in _suppress_next:
                _suppress_next.discard(record.project_id)
                # User-initiated stop → don't count this as a crash, and
                # reset the retry counter so a later crash starts fresh.
                _retry_counts.pop(record.project_id, None)
                continue
            if record.kind == "dependency":
                # A dep dying isn't a crash event for the project — its
                # exit is just a status change in Edit Details. The main
                # process keeps running; sweep_dead already removed the
                # row, and one-shot deps with `completed_at` set are
                # preserved for UI display by sweep_dead's skip rule.
                continue
            # Main process died — tear down companion deps that didn't
            # opt into surviving the main, regardless of whether we go
            # on to crash-relaunch. This keeps deps from accumulating
            # across relaunches and stops them from spamming errors at
            # the now-gone main.
            _terminate_dependents(record.project_id)
            _schedule_relaunch(record)
        except Exception:  # noqa: BLE001
            pass

    # Pass 2 — hang detection on still-alive monitored processes.
    now = time.time()
    alive_pids = {rec.pid for rec in alive}
    # Drop hang tracking for pids we no longer see alive.
    for stale_pid in [p for p in _hang_first_seen if p not in alive_pids]:
        _hang_first_seen.pop(stale_pid, None)

    for record in process_tracker.active(alive):
        try:
            _check_for_hang(record, now)
        except Exception:  # noqa: BLE001
            pass


def _check_for_hang(record: ProcessRecord, now: float) -> None:
    entry = library.get(record.project_id)
    if entry is None or entry.project is None:
        _hang_first_seen.pop(record.pid, None)
        return
    proj = entry.project
    # Auto Relaunch requires Auto Launch — the dialog enforces this UI
    # invariant, but we double-check at runtime so a legacy config with
    # reopen_on_crash=True but launch_on_startup=False doesn't resurrect.
    if not (proj.launch_on_startup and proj.reopen_on_crash):
        _hang_first_seen.pop(record.pid, None)
        return

    age = _record_age_seconds(record, now)
    if age < float(proj.startup_grace_seconds):
        # Still inside the startup grace window — TD can take ~60s to
        # boot and often looks unresponsive during load. Don't poke.
        _hang_first_seen.pop(record.pid, None)
        return

    frozen = process_tracker.is_process_frozen(record.pid)
    if frozen is True:
        first = _hang_first_seen.setdefault(record.pid, now)
        if now - first >= float(proj.freeze_timeout_seconds):
            # Confirmed hung long enough — force it down and relaunch
            # through the crash path. Short terminate timeout because a
            # frozen process probably won't respond to SIGTERM anyway.
            _hang_first_seen.pop(record.pid, None)
            try:
                process_tracker.terminate_process(record.pid, timeout=1.0)
            except Exception:  # noqa: BLE001
                pass
            _schedule_relaunch(record)
    else:
        # Responsive, or we couldn't tell (None on non-Windows / no
        # window yet) — don't accumulate hang time.
        _hang_first_seen.pop(record.pid, None)


def _terminate_dependents(project_id: int) -> None:
    """Kill the project's deps that didn't opt into surviving main.

    Called when a project's main process dies (clean or crashed). Deps
    with `keep_alive_after_main=True` are left running — the user
    explicitly asked for them to outlive the main app. Other deps are
    terminated so they don't pile up across crashes.
    """
    try:
        records = process_tracker.list_for_project(project_id)
    except Exception:  # noqa: BLE001
        return
    for rec in process_tracker.active(records):
        if rec.kind != "dependency":
            continue
        if rec.keep_alive_after_main:
            continue
        try:
            process_tracker.terminate_process(rec.pid, timeout=2.0)
        except Exception:  # noqa: BLE001
            pass


def _schedule_relaunch(record: ProcessRecord) -> None:
    row_id = record.project_id
    if row_id in _pending_relaunch:
        return  # already armed
    entry = library.get(row_id)
    if entry is None or entry.project is None:
        return
    proj = entry.project
    # Auto Relaunch requires Auto Launch — see _check_for_hang for
    # the same reason. Both must be on for the relaunch path to fire.
    if not (proj.launch_on_startup and proj.reopen_on_crash):
        return
    if proj.max_retries_enabled:
        count = _retry_counts.get(row_id, 0)
        if count >= int(proj.max_retries):
            # Cap hit — stop auto-relaunching. User must manually
            # intervene (clicking Launch resets the counter).
            return

    _retry_counts[row_id] = _retry_counts.get(row_id, 0) + 1
    _pending_relaunch.add(row_id)
    delay_ms = max(0, int(proj.relaunch_delay_seconds) * 1000)
    # Capture the record by default-arg so the closure doesn't pick up
    # a later iteration's binding.
    QTimer.singleShot(
        delay_ms,
        lambda rid=row_id, rec=record: _do_relaunch(rid, rec),
    )


def _do_relaunch(row_id: int, record: ProcessRecord) -> None:
    _pending_relaunch.discard(row_id)
    try:
        entry = library.get(row_id)
    except Exception:  # noqa: BLE001
        return
    if entry is None or entry.project is None:
        return
    # Race guard — user may have manually launched during the cooldown,
    # or another relaunch already fired. Either way, don't double-spawn.
    # Only the main process counts here: surviving `keep_alive` deps
    # (or a slow-to-die dep we already asked to terminate) shouldn't
    # block a fresh main launch.
    try:
        running = process_tracker.active(process_tracker.list_for_project(row_id))
    except Exception:  # noqa: BLE001
        return
    if any(r.kind != "dependency" for r in running):
        return
    if not (entry.project.launch_on_startup and entry.project.reopen_on_crash):
        return
    try:
        launch.launch_toe(entry, record.toe_file)
    except launch.NeedsApproval:
        # Its run settings changed while it was running (e.g. a git pull)
        # and haven't been reviewed: don't run them unattended.
        approval.notify_held([entry.project.name or entry.path.name])
    except (launch.LaunchError, launch.VersionMismatch):
        # Can't relaunch right now (missing .toe, no install, pinned
        # version gone). Leave the counter where it is so the next
        # crash still counts; the user will notice idle-state and can
        # manually intervene.
        pass
    except Exception:  # noqa: BLE001
        pass


def _record_age_seconds(record: ProcessRecord, now: float) -> float:
    """Seconds since ``record.started_at`` (ISO with tz).

    Returns +inf on parse failure — treat as "been running a long time,
    outside any grace window" which is the safer default (won't suppress
    hang detection).
    """
    if not record.started_at:
        return float("inf")
    try:
        launched = datetime.fromisoformat(record.started_at)
    except (ValueError, TypeError):
        return float("inf")
    if launched.tzinfo is None:
        launched_ts = launched.timestamp()
    else:
        launched_ts = launched.timestamp()
    return max(0.0, now - launched_ts)
