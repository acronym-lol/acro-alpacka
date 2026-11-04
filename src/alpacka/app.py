"""QApplication bootstrap."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QFileSystemWatcher, QObject, Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from . import (
    approval,
    db,
    launch,
    library,
    output_manager,
    paths,
    process_tracker,
    td_install,
    templates,
)
from .ui.main_window import MainWindow


class _PointerCursorFilter(QObject):
    """Pointer cursor on every QPushButton; plain text on every QLabel.

    QSS doesn't support a `cursor` property, and manually calling
    ``setCursor(Qt.PointingHandCursor)`` on every button drifts out of
    sync. An app-level event filter catches each button's ``Polish``
    event (fired once, just before the button is first shown) and sets
    the cursor for us — works for buttons in existing views, in dialogs
    that open later, and in cards rebuilt on every refresh.
    """

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.Polish:
            if isinstance(obj, QPushButton):
                obj.setCursor(Qt.CursorShape.PointingHandCursor)
            elif isinstance(obj, QLabel):
                # Labels show project names, descriptions, commands and
                # versions from alpacka.yml files that can come from
                # anyone. Qt's default (AutoText) would render HTML in
                # them; nothing in ALPACKA uses rich text, so all labels
                # are plain text.
                obj.setTextFormat(Qt.TextFormat.PlainText)
        return False


def _load_stylesheet() -> str:
    here = Path(__file__).resolve().parent
    qss = here / "ui" / "style.qss"
    if not qss.exists():
        return ""
    content = qss.read_text(encoding="utf-8")
    # Substitute absolute icon paths. Qt accepts POSIX-style paths via url().
    icons_dir = (here / "resources" / "icons").as_posix()
    return content.replace("{ICONS}", icons_dir)


def _install_stylesheet_watcher(app: QApplication) -> QFileSystemWatcher | None:
    """Watch ``style.qss`` and re-apply on save.

    Many editors (VS Code, vim with backups, etc.) perform saves as
    rename-over operations — the original inode is replaced, and Qt's
    watcher loses the path after the first change. We re-add the path
    on every fire to keep tracking.
    """
    qss_path = Path(__file__).resolve().parent / "ui" / "style.qss"
    if not qss_path.is_file():
        return None

    watcher = QFileSystemWatcher([str(qss_path)])

    def _reload():
        new_ss = _load_stylesheet()
        if new_ss:
            app.setStyleSheet(new_ss)
        if str(qss_path) not in watcher.files():
            watcher.addPath(str(qss_path))

    watcher.fileChanged.connect(_reload)
    return watcher


def _ensure_dpi_awareness() -> None:
    """Mark the process per-monitor-v2 DPI aware on Windows.

    Must run before ``QApplication`` (and any window) is created. Matches the
    per-monitor-v2 awareness ``python.exe`` already declares (and that the
    embedded manifest sets — see ``build.py``); this call is a safety net for
    launch paths the manifest doesn't cover. Tries the strongest API first and
    falls back for older Windows; each call no-ops (without raising) when
    awareness is already locked.
    """
    if sys.platform != "win32":
        return
    import ctypes

    # Preferred (Win10 1703+): per-monitor-v2 via a DPI_AWARENESS_CONTEXT
    # pseudo-handle. -4 == DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2.
    # argtypes matters on 64-bit: without it ctypes would pass the handle as
    # a 32-bit int and the call would be rejected.
    try:
        fn = ctypes.windll.user32.SetProcessDpiAwarenessContext
        fn.restype = ctypes.c_bool
        fn.argtypes = [ctypes.c_void_p]
        if fn(ctypes.c_void_p(-4)):
            return
    except (OSError, AttributeError):
        pass

    # Fallback (Win8.1+): shcore per-monitor. PROCESS_PER_MONITOR_DPI_AWARE=2.
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:  # S_OK
            return
    except (OSError, AttributeError):
        pass

    # Last resort (Vista+): system aware.
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (OSError, AttributeError):
        pass


def run(argv: list[str]) -> int:
    _ensure_dpi_awareness()
    paths.ensure_dirs()
    db.init()
    templates.ensure_builtins()
    # Projects that were in the library before run-settings approval existed
    # are approved as they are now, so an update doesn't stop auto-launch.
    try:
        approval.grandfather_existing()
    except Exception:  # noqa: BLE001 — never block startup
        pass

    # Purge any stale PID rows from a previous session BEFORE auto-launch
    # so we don't double-launch a project that the user still has open.
    process_tracker.sweep_dead()
    # Finished one-shot runs from the last session: their exit codes have
    # been shown, and their PIDs may since belong to other programs.
    process_tracker.clear_completed()

    app = QApplication(argv)
    app.setApplicationName("ALPACKA")
    app.setOrganizationName("Acronym")

    # Set an application-wide default font with an explicit point size BEFORE
    # loading the stylesheet. The stylesheet uses pixel-unit font-sizes
    # throughout; without a baseline point size set here, Qt's internal
    # style plumbing (notably the QComboBox popup's QListView font
    # resolution) warns "QFont::setPointSize: Point size <= 0 (-1)" when
    # it reads `pointSize()` on a widget whose font only has a pixel size.
    # A valid default silences the warning without overriding the QSS
    # pixel-size rules.
    default_font = QFont(app.font())
    if default_font.pointSize() <= 0:
        default_font.setPointSize(10)
        app.setFont(default_font)

    icon_path = Path(__file__).resolve().parent / "resources" / "icons" / "alpacka.ico"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))

    cursor_filter = _PointerCursorFilter(app)
    app.installEventFilter(cursor_filter)

    stylesheet = _load_stylesheet()
    if stylesheet:
        app.setStyleSheet(stylesheet)

    # Dev convenience: live-reload the stylesheet when `style.qss` is
    # saved. Harmless in production (the packaged file never changes).
    # Reference is stashed on the app so the watcher isn't GC'd.
    app._style_watcher = _install_stylesheet_watcher(app)

    window = MainWindow()
    window.show()

    # Inline-mode children are tied to ALPACKA's session lifetime —
    # when the app quits, terminate them gracefully (SIGTERM /
    # CTRL_BREAK_EVENT, brief wait, SIGKILL). Documented contract;
    # console + silent launches are unaffected (they're independent).
    app.aboutToQuit.connect(output_manager.shutdown_inline_children)

    # Auto-launch any projects flagged `launch_on_startup` that aren't
    # already tracked as running. Wrapped so a failing project doesn't
    # stop subsequent ones from launching.
    _auto_launch_flagged()

    return app.exec()


def _auto_launch_flagged() -> None:
    """Open every project with `launch_on_startup=True` that isn't running.

    TD projects resolve their pinned install silently (no mismatch dialog
    on boot); other kinds dispatch through the generic `launch.launch`
    path using the handler's default run config.
    """
    installs = td_install.detect()
    default_install = installs[0] if installs else None
    held: list[str] = []

    for entry in library.list_entries():
        proj = entry.project
        if proj is None or not proj.launch_on_startup:
            continue
        if process_tracker.active(entry.running_processes):
            continue  # already open from a previous launcher session
        # Never auto-run settings nobody has reviewed on this machine; the
        # main window's banner says which projects are waiting.
        if not approval.is_approved(entry):
            held.append(proj.name or entry.path.name)
            continue

        try:
            if proj.kind != "touchdesigner":
                launch.launch(entry)
                continue

            if not proj.toe_file:
                continue

            install = None
            if proj.td_version:
                install = td_install.find_by_version(installs, proj.td_version)
            if install is None:
                install = default_install
            if install is None:
                continue

            launch.launch_toe(entry, proj.toe_file, install=install)
        except launch.NeedsApproval:
            held.append(proj.name or entry.path.name)
        except Exception:  # noqa: BLE001
            # Non-fatal (LaunchError, VersionMismatch, a broken kind
            # handler…). User will see the project un-launched; they can
            # click Launch manually to get an error in the UI. One bad
            # project must not stop the others or startup itself.
            continue

    approval.notify_held(held)
