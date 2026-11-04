"""Per-project capture buffers for inline-mode launches.

When a project is launched with ``output_mode == "inline"``, launch.py
hands the resulting ``Popen`` to :func:`attach` along with the
project's library row id. From that point:

  - A :class:`_CaptureWorker` (QThread) reads the process's stdout
    line-by-line and forwards each line to the matching
    :class:`OutputBuffer`.
  - The buffer holds a ring of recent lines and emits Qt signals so any
    open :class:`OutputView` dialog stays in sync live.
  - When the process exits, :func:`notify_exit` flips the buffer's
    status to ``("exited", code)`` so the UI can render a final state.

Lifecycle decisions, locked in the design plan:

  - **Bounded memory.** Each buffer is a ring of the last 5000 lines.
    A chatty server doesn't grow without limit.
  - **No disk persistence.** Buffers are session-only; closing ALPACKA
    drops them. Documented as the inline-mode contract.
  - **Children die with ALPACKA.** :func:`shutdown_inline_children`
    is wired to ``QApplication.aboutToQuit`` so the app's process tree
    is reaped on exit instead of being orphaned.
  - **No Qt access from worker threads.** Lines arrive via queued
    signals, so the GUI thread is the only one touching widgets.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from collections import deque
from typing import Any

from PySide6.QtCore import QObject, QThread, Qt, Signal


# Maximum lines kept per project. Old lines silently age out as new ones
# arrive — the dialog can scroll back up to ~5000 entries before the
# tail rolls forward.
_RING_CAPACITY = 5000

# How long to wait for graceful shutdown of an inline child during
# `aboutToQuit` before escalating to SIGKILL.
_GRACEFUL_TIMEOUT_SECONDS = 3.0

# Line-emission cadence on the capture worker. Higher = fewer Qt
# signals (less event-loop pressure) but choppier visible updates;
# 50ms keeps things smooth without flooding.
_FLUSH_INTERVAL_SECONDS = 0.05


class OutputBuffer(QObject):
    """Live, bounded log for a single project's captured output.

    A single instance lives per ``library_entry.row_id`` for the life of
    the app session. Dialogs subscribe to :attr:`lines_appended` for
    incremental updates and call :meth:`snapshot` once on open to
    rehydrate from the existing ring.
    """

    # Each emission is a list of consecutive lines (worker batches at
    # ~50ms cadence). Listeners append the entire batch at once.
    lines_appended = Signal(list)
    # Status transitions: ("idle"|"running"|"exited", exit_code_or_-1).
    status_changed = Signal(str, int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._lines: deque[str] = deque(maxlen=_RING_CAPACITY)
        self._status: str = "idle"
        self._exit_code: int = -1

    # --- Status -----------------------------------------------------------

    def status(self) -> str:
        with self._lock:
            return self._status

    def exit_code(self) -> int:
        with self._lock:
            return self._exit_code

    def set_running(self) -> None:
        with self._lock:
            self._status = "running"
            self._exit_code = -1
        self.status_changed.emit("running", -1)

    def set_exited(self, code: int) -> None:
        with self._lock:
            self._status = "exited"
            self._exit_code = int(code)
        self.status_changed.emit("exited", int(code))

    # --- Content ----------------------------------------------------------

    def append_lines(self, lines: list[str]) -> None:
        """Append a batch of captured lines and notify subscribers.

        Called from the capture worker via a queued signal connection,
        so this runs on the GUI thread — no extra locking needed for
        the emit, but the deque itself is locked for snapshot safety.
        """
        if not lines:
            return
        with self._lock:
            self._lines.extend(lines)
        self.lines_appended.emit(list(lines))

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()
        # Subscribers re-render via snapshot on next signal; the empty
        # list emit is a cheap "you should reload" nudge.
        self.lines_appended.emit([])

    def snapshot(self) -> tuple[list[str], str, int]:
        """Atomically read all current lines + status + exit code.

        Dialogs call this once on open under the same lock that
        :meth:`append_lines` holds, so the subsequent signal-driven
        appends can't race past the snapshot's tail.
        """
        with self._lock:
            return list(self._lines), self._status, self._exit_code


class _CaptureWorker(QThread):
    """Read a Popen's stdout line-by-line and forward to a buffer.

    Lines are accumulated in a thread-local list and emitted in batches
    every :data:`_FLUSH_INTERVAL_SECONDS` (or when the read blocks
    naturally on an idle process). One queued ``Signal(list)`` per
    flush keeps the GUI thread unflooded even under chatty servers.

    `source` is an optional label that, when set, is prefixed to every
    captured line as ``[<source>] line`` so downstream viewers can
    distinguish the main process from companion launch dependencies
    sharing the same project's buffer. Empty / None ⇒ no prefix.
    """

    batch = Signal(list)
    finished_with_code = Signal(int)

    def __init__(
        self,
        proc: subprocess.Popen,
        parent: QObject | None = None,
        *,
        source: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._proc = proc
        self._stop = threading.Event()
        self._pending: list[str] = []
        self._pending_lock = threading.Lock()
        self._source = (source or "").strip() or None
        self._prefix = f"[{self._source}] " if self._source else ""

    def request_stop(self) -> None:
        """Caller-initiated shutdown nudge.

        Doesn't kill the process — that's the caller's job. Just signals
        the read loop to bail after the next flush so the thread joins
        cleanly during ``aboutToQuit``.
        """
        self._stop.set()

    def run(self) -> None:
        # Spawn a sibling timer thread that flushes pending lines on a
        # cadence even when the readline loop is blocking on stdout. We
        # use a plain Thread (not QThread) since this runs OFF the Qt
        # event loop and does not touch widgets — the flush emits the
        # signal which is queued back to the GUI thread.
        flusher = threading.Thread(
            target=self._flush_loop, name=f"alpacka-flush-{self._proc.pid}", daemon=True
        )
        flusher.start()

        try:
            if self._proc.stdout is not None:
                for raw in self._proc.stdout:
                    if self._stop.is_set():
                        break
                    line = raw.rstrip("\r\n")
                    if self._prefix:
                        line = self._prefix + line
                    with self._pending_lock:
                        self._pending.append(line)
        except (OSError, ValueError):
            # Pipe closed under us — fine, exit the loop and report.
            pass

        # Final flush to drain anything queued after the read loop
        # bailed (EOF from the child usually leaves zero-to-N lines).
        self._stop.set()
        flusher.join(timeout=0.5)
        self._flush_now()

        try:
            exit_code = self._proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            exit_code = -1
        self.finished_with_code.emit(int(exit_code))

    def _flush_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(_FLUSH_INTERVAL_SECONDS)
            self._flush_now()

    def _flush_now(self) -> None:
        with self._pending_lock:
            if not self._pending:
                return
            batch = self._pending
            self._pending = []
        self.batch.emit(batch)


# ---------------------------------------------------------------------------
# Module-level registry — one buffer per row_id, multiple captures per row
# ---------------------------------------------------------------------------
#
# `_BUFFERS` is keyed by row_id (one shared buffer per project so deps
# stream into the same OutputView the main launch uses). `_WORKERS` and
# `_PROCS` are keyed by `(row_id, pid)` so multiple inline captures —
# one main + N deps — coexist without overwriting each other.


_BUFFERS: dict[int, OutputBuffer] = {}
_WORKERS: dict[tuple[int, int], _CaptureWorker] = {}
_PROCS: dict[tuple[int, int], subprocess.Popen] = {}
_LOCK = threading.Lock()


def get_buffer(row_id: int) -> OutputBuffer:
    """Return (creating if needed) the buffer for `row_id`.

    Buffers persist across launches of the same project so a closed-and-
    reopened OutputView can rehydrate the previous session's output if
    nothing has been launched yet. New launches clear before capturing.
    """
    with _LOCK:
        buf = _BUFFERS.get(row_id)
        if buf is None:
            buf = OutputBuffer()
            _BUFFERS[row_id] = buf
        return buf


def has_buffer(row_id: int) -> bool:
    """True if any inline launch has ever attached for this project."""
    with _LOCK:
        return row_id in _BUFFERS


def attach(
    row_id: int, proc: subprocess.Popen, *, source: str | None = None
) -> None:
    """Wire an inline-mode subprocess into the per-project buffer.

    `source` is an optional label that prefixes every captured line so
    multiple processes for the same project (a main launch + companion
    launch dependencies) can be distinguished in the merged output. The
    main launch attaches without a source; deps pass their dep label.

    The shared buffer is cleared only on the first attach for a row_id
    in this run — i.e. when no other capture is currently active for
    the project. That way a main launch (which attaches first) starts
    with a clean buffer, and the deps that follow append to the same
    fresh buffer rather than wiping the main's first lines.

    `(row_id, pid)` is the registry key so multiple workers coexist;
    a re-attach for the same `(row_id, pid)` (rare — same process
    reusing the same PID) replaces the prior worker, which has by
    contract already exited.
    """
    buf = get_buffer(row_id)

    # Only clear the buffer when there's no other live capture for the
    # project. The main launch attaches first (no peer captures), so it
    # gets a clean buffer. Deps attach a moment later and append to it.
    with _LOCK:
        peer_active = any(rid == row_id for (rid, _pid) in _WORKERS.keys())
    if not peer_active:
        buf.clear()
        buf.set_running()

    worker = _CaptureWorker(proc, source=source)
    # Queued connection: worker.batch fires on the worker's thread; the
    # buffer's slot runs on the GUI thread (its `thread()` affinity).
    worker.batch.connect(buf.append_lines, Qt.ConnectionType.QueuedConnection)
    # Only the main capture (no source label) controls the buffer's exit
    # status — a dep finishing shouldn't flip the project-level pill to
    # "exited" while the main process is still running.
    if source is None:
        worker.finished_with_code.connect(
            lambda code, b=buf: b.set_exited(code),
            Qt.ConnectionType.QueuedConnection,
        )

    key = (row_id, proc.pid)
    with _LOCK:
        prior = _WORKERS.pop(key, None)
        if prior is not None and prior.isRunning():
            prior.request_stop()
            prior.wait(500)
        _PROCS.pop(key, None)
        _WORKERS[key] = worker
        _PROCS[key] = proc
    # Reap our own registration when the worker thread exits, so a long
    # idle session doesn't accumulate dead entries.
    worker.finished_with_code.connect(
        lambda _code, k=key: _reap_worker(k),
        Qt.ConnectionType.QueuedConnection,
    )

    worker.start()


def _reap_worker(key: tuple[int, int]) -> None:
    """Drop a finished worker's registry entry."""
    with _LOCK:
        _WORKERS.pop(key, None)
        _PROCS.pop(key, None)


def notify_exit(row_id: int, code: int) -> None:
    """Update the buffer's exit status without depending on the worker.

    Called from launch._watch_completion when a tracked one-shot exits.
    Long-running inline processes get the same notification through
    the worker's `finished_with_code` signal, but going through this
    function explicitly keeps the API symmetric for non-inline paths
    that still want to cap the buffer cleanly.
    """
    buf = _BUFFERS.get(row_id)
    if buf is not None:
        buf.set_exited(int(code))


def shutdown_inline_children() -> None:
    """Terminate every inline child cleanly. Wired to QApplication.aboutToQuit.

    Sends a graceful signal first (CTRL_BREAK on Windows, SIGTERM via
    process-group on POSIX), waits up to a few seconds, then escalates
    to SIGKILL. The capture workers are signalled to stop reading so
    the threads join during normal Qt teardown.
    """
    with _LOCK:
        items = list(_PROCS.items())  # [((row_id, pid), proc), ...]

    for key, proc in items:
        worker = _WORKERS.get(key)
        if worker is not None:
            worker.request_stop()
        if proc.poll() is not None:
            continue
        _send_graceful_signal(proc)

    # Wait briefly, then force-kill anything still standing.
    deadline = _GRACEFUL_TIMEOUT_SECONDS
    waited = 0.0
    while waited < deadline:
        if all(p.poll() is not None for _, p in items):
            break
        threading_sleep(0.1)
        waited += 0.1

    for _key, proc in items:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    for key, worker in list(_WORKERS.items()):
        worker.wait(500)
        with _LOCK:
            _WORKERS.pop(key, None)
            _PROCS.pop(key, None)


def _send_graceful_signal(proc: subprocess.Popen) -> None:
    """Send a polite shutdown signal to `proc` (and its group on POSIX).

    Windows: CTRL_BREAK_EVENT to the process group (requires the
    process was spawned with CREATE_NEW_PROCESS_GROUP — `run_inline`
    does this).
    POSIX: SIGTERM to the session group via `os.killpg` (requires
    `start_new_session=True` at spawn — `run_inline` does this).
    Falls back to `proc.terminate()` on any failure.
    """
    try:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (OSError, ValueError, AttributeError):
        try:
            proc.terminate()
        except OSError:
            pass


def threading_sleep(seconds: float) -> None:
    """Indirection so test code can monkey-patch sleep without touching time."""
    import time as _time
    _time.sleep(seconds)


__all__ = [
    "OutputBuffer",
    "attach",
    "get_buffer",
    "has_buffer",
    "notify_exit",
    "shutdown_inline_children",
]
