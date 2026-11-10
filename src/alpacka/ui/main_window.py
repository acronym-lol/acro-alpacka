"""Main launcher window — frameless shell with custom title bar,
sidebar navigation, and stacked content views."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QIcon, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizeGrip,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class _VisibleSizeGrip(QSizeGrip):
    """QSizeGrip that actually draws something visible.

    The native grip is invisible against our dark theme, so we paint
    a standard 3-row diagonal dot pattern in the bottom-right corner.
    The underlying QSizeGrip handles the resize interaction.
    """

    _DOT_COLOR = QColor("#9D948B")  # MUD PUDDLE

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._DOT_COLOR)

        w = self.width()
        h = self.height()
        step = 4
        radius = 1.3
        # Triangular dot grid: row 0 has 3 dots, row 1 has 2, row 2 has 1.
        for row in range(3):
            for col in range(3 - row):
                cx = w - 3 - col * step
                cy = h - 3 - row * step
                painter.drawEllipse(QPointF(cx, cy), radius, radius)
        painter.end()

from .. import __version__, approval, library, process_tracker, settings, td_install, td_prefs, updater
from . import frameless, update_dialog
from .add_existing_dialog import AddExistingDialog
from .edit_details_view import EditDetailsView
from .library_view import LibraryView
from .new_project_view import NewProjectView
from .settings_view import SettingsView
from .sidebar_tree import SidebarTree
from .td_prefs_dialog import TDPrefsPromptDialog
from .templates_view import TemplatesView
from .title_bar import TitleBar


class _NoticeBanner(QFrame):
    """Dismissible warning strip above the content views.

    Used to tell the user when ALPACKA changed TouchDesigner preferences
    during a launch (most often: set back a value that was changed
    outside ALPACKA). Never modal — launches are often unattended.
    """

    def __init__(self, on_open_settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("noticeBanner")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 12, 10)
        layout.setSpacing(10)
        self._label = QLabel()
        self._label.setObjectName("noticeBannerText")
        self._label.setWordWrap(True)
        layout.addWidget(self._label, stretch=1)
        self._open_btn = QPushButton("Open Settings")
        self._open_btn.clicked.connect(lambda _checked=False: on_open_settings())
        layout.addWidget(self._open_btn)
        dismiss = QPushButton("Dismiss")
        dismiss.clicked.connect(lambda _checked=False: self.hide())
        layout.addWidget(dismiss)
        self.hide()

    def show_message(self, text: str, *, settings_link: bool = True) -> None:
        self._label.setText(text)
        self._open_btn.setVisible(settings_link)
        self.show()

# Size in pixels of the invisible border used for edge resize. Needs to be
# wide enough to grab easily with the mouse but not so wide that it eats
# clicks meant for content near the window edge.
_RESIZE_BORDER = 8


_ICONS_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"
_APP_ICO = _ICONS_DIR / "alpacka.ico"
_APP_LOGO_SVG = _ICONS_DIR / "alpacka-logo.svg"

# Once per day. The settings view's "Check now" bypasses this throttle.
_UPDATE_THROTTLE_SEC = 24 * 60 * 60
# Small delay so the first paint completes before the network call fires.
_UPDATE_STARTUP_DELAY_MS = 2000


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ALPACKA")
        self.resize(1360, 860)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        if _APP_ICO.is_file():
            self.setWindowIcon(QIcon(str(_APP_ICO)))

        self.settings = settings.load()
        self._seed_td_installs_if_empty()

        # Modeless output dialogs, keyed by library row id. Persists
        # across library refreshes so a user can keep a tail open
        # while ALPACKA rebuilds card grids underneath.
        self._output_views: dict[int, "OutputView"] = {}

        self._build_ui()

        # Launch-time pref.txt changes surface as a banner. Subscribed
        # before app.py's auto-launch so those launches are reported too.
        td_prefs.subscribe(self._on_td_prefs_result)
        # Same for projects held back from auto-launch / crash relaunch
        # because their run settings need reviewing (approval.py).
        approval.subscribe(self._on_launch_held)

        # Startup prompts run once the event loop starts — i.e. after
        # app.py's auto-launch, so a pending prompt never holds up an
        # unattended show machine. The update check follows the TD
        # preferences prompt so the two dialogs never stack.
        QTimer.singleShot(0, self._run_startup_prompts)


    # --- Construction ---

    def _seed_td_installs_if_empty(self) -> None:
        """On first run (or when settings were cleared), populate td_installs."""
        if self.settings.td_installs:
            return
        detected = td_install.detect()
        if not detected:
            return
        self.settings.td_installs = detected
        self.settings.default_td_install = detected[0].version
        settings.save(self.settings)

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("rootContainer")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.title_bar = TitleBar(self, "ALPACKA", icon_path=_APP_LOGO_SVG)
        outer.addWidget(self.title_bar)

        # QSplitter lets the user drag the sidebar/content boundary to
        # accommodate deeply-nested tags or long tag names. Stretch
        # factors pin the initial split to settings.sidebar_width while
        # sending window resizes to the content pane.
        self.body_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.body_splitter.setObjectName("bodySplitter")
        self.body_splitter.setHandleWidth(2)
        self.body_splitter.setChildrenCollapsible(False)
        self.body_splitter.addWidget(self._build_sidebar())
        self.body_splitter.addWidget(self._build_content())
        self.body_splitter.setStretchFactor(0, 0)
        self.body_splitter.setStretchFactor(1, 1)
        initial_sidebar = max(140, int(self.settings.sidebar_width or 180))
        self.body_splitter.setSizes([initial_sidebar, 9999])
        self.body_splitter.splitterMoved.connect(self._on_splitter_moved)
        outer.addWidget(self.body_splitter, stretch=1)

        # Always-visible bottom-right resize handle. WM_NCHITTEST on
        # Windows covers the four edges, but a visible grip is a stronger
        # signal that the window resizes — especially with a frameless
        # chrome that gives no other hint. Subclassed grip paints an
        # actual dot pattern so it's legible on the dark theme.
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 2, 2)
        grip_row.addStretch(1)
        grip = _VisibleSizeGrip(self)
        grip.setObjectName("mainSizeGrip")
        grip_row.addWidget(grip)
        outer.addLayout(grip_row)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        # Drop the fixed width so the enclosing QSplitter can resize us.
        # Minimum keeps the three nav items legible; no maximum means
        # the user can go as wide as they like for long tag names.
        sidebar.setMinimumWidth(140)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 20, 12, 12)
        layout.setSpacing(4)

        self.sidebar_tree = SidebarTree()
        self.sidebar_tree.selection_changed.connect(self._on_sidebar_selected)
        self.sidebar_tree.tag_edit_requested.connect(self._on_tag_edit)
        self.sidebar_tree.tag_delete_requested.connect(self._on_tag_delete)
        self.sidebar_tree.tag_add_requested.connect(self._on_tag_add)
        layout.addWidget(self.sidebar_tree, stretch=1)

        version_label = QLabel(f"v{__version__}")
        version_label.setObjectName("versionLabel")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version_label)

        return sidebar

    def _build_content(self) -> QWidget:
        container = QWidget()
        container.setObjectName("content")

        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.library_view = LibraryView()
        self.library_view.new_project_requested.connect(self._on_new_project)
        self.library_view.add_existing_requested.connect(self._on_add_existing)
        self.library_view.edit_details_requested.connect(
            self._on_open_edit_details
        )
        self.library_view.view_output_requested.connect(
            self._on_view_output
        )
        # Clicking a tag tile or breadcrumb segment in the content area
        # should update the sidebar tree selection to keep them in sync.
        self.library_view.navigate_requested.connect(
            self._on_library_navigate
        )
        # Card-level tag edits create new tags in `tags.json`; the tree
        # needs to pick those up.
        self.library_view.tags_store_changed.connect(
            self.sidebar_tree.rebuild
        )
        # Reverse direction — dragging a tag in the sidebar reorders
        # `tags.json`, and the content-area tiles cache the previous
        # tree shape. A refresh reloads both.
        self.sidebar_tree.tag_tree_changed.connect(
            self.library_view.refresh
        )
        # Project cards dropped onto sidebar tags go through the
        # library-view handler that knows the current tag context.
        self.sidebar_tree.project_drop_requested.connect(
            self.library_view.apply_project_tag_drop
        )
        self.templates_view = TemplatesView()
        self.settings_view = SettingsView(self.settings)
        self.settings_view.settings_changed.connect(self._on_settings_changed)

        self.edit_details_view = EditDetailsView()
        self.edit_details_view.saved.connect(self._on_edit_details_saved)
        self.edit_details_view.cancelled.connect(self._on_edit_details_cancelled)

        self.new_project_view = NewProjectView()
        self.new_project_view.created.connect(self._on_new_project_created)
        self.new_project_view.cancelled.connect(self._on_new_project_cancelled)

        self.notice_banner = _NoticeBanner(self._open_settings)
        layout.addWidget(self.notice_banner)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.library_view)       # index 0
        self.stack.addWidget(self.templates_view)     # index 1
        self.stack.addWidget(self.settings_view)      # index 2
        self.stack.addWidget(self.edit_details_view)  # index 3
        self.stack.addWidget(self.new_project_view)   # index 4
        layout.addWidget(self.stack, stretch=1)

        # LibraryView.__init__ runs refresh() which may auto-add tag
        # names to tags.json (see library.refresh_stale). The sidebar
        # tree was constructed before that, so give it a rebuild now —
        # after self.stack exists — so the selection handler can run.
        self.sidebar_tree.rebuild()

        return container

    # --- Slots ---

    def _on_sidebar_selected(self, kind: str, path: list) -> None:
        if kind == "library":
            self.stack.setCurrentIndex(0)
            self.library_view.set_current_path([])
        elif kind == "tag":
            self.stack.setCurrentIndex(0)
            self.library_view.set_current_path(list(path))
        elif kind == "templates":
            self.stack.setCurrentIndex(1)
            # Refresh the Templates view every time it becomes visible so it
            # reflects built-in path changes (e.g., a newly-installed TD
            # version makes the blank built-in resolvable).
            self.templates_view.refresh()
        elif kind == "settings":
            self.stack.setCurrentIndex(2)
            # pref.txt may have changed (TD rewrites it), so show fresh values.
            self.settings_view.refresh_td_prefs()

    def _on_library_navigate(self, path: list) -> None:
        """Content-area tiles/breadcrumbs → sidebar tree stays in sync."""
        self.sidebar_tree.select_tag_path(list(path))

    def _on_tag_add(self, parent_name) -> None:
        """Route sidebar add-tag clicks through the LibraryView
        handler so it shares the dialog + refresh path used elsewhere."""
        self.library_view.apply_tag_add(parent_name)

    def _on_tag_edit(self, tag_name: str) -> None:
        """Open the rename/color dialog for a tag and persist on save."""
        from .. import library as library_mod
        from .. import project as project_io
        from .. import tags
        from .tag_edit_dialog import TagEditDialog

        tree = tags.load()
        node = tags.find(tree, tag_name)
        current_color = node.color if node is not None else ""
        result = TagEditDialog.edit(self, tag_name, current_color)
        if result is None:
            return
        new_name, new_color = result

        # Apply rename first (raises on dup / blank), then color. Any
        # failure here bails without partial mutation of the tree.
        try:
            if new_name != tag_name:
                tags.rename_tag(tree, tag_name, new_name)
            tags.set_tag_color(tree, new_name, new_color)
        except tags.TagError as exc:
            frameless.warning(self, "Could not update tag", str(exc))
            return
        tags.save(tree)

        # Propagate a rename to every alpacka.yml that referenced the
        # old name — the tag lists on projects are source-of-truth for
        # filter membership, so they must stay in sync.
        if new_name != tag_name:
            for entry in library_mod.list_entries():
                if entry.project and tag_name in entry.project.tags:
                    proj = project_io.load(entry.path)
                    if proj is None:
                        continue
                    proj.tags = [
                        new_name if t == tag_name else t for t in proj.tags
                    ]
                    try:
                        project_io.save(proj)
                    except OSError:
                        pass

        self.sidebar_tree.rebuild()
        # If the renamed tag was the currently-viewed path, follow it.
        current = self.library_view.current_path()
        if tag_name in current:
            new_path = [new_name if n == tag_name else n for n in current]
            self.sidebar_tree.select_tag_path(new_path)
        self.library_view.refresh()

    def _on_tag_delete(self, tag_name: str) -> None:
        """Confirm + delete a tag subtree, scrubbing it from all projects."""
        from .. import library as library_mod
        from .. import project as project_io
        from .. import tags

        tree = tags.load()
        # Count how many projects use this tag (or any descendant).
        subtree = tags.descendants_of(tree, tag_name) | {tag_name}
        affected = [
            e
            for e in library_mod.list_entries()
            if e.project and set(e.project.tags) & subtree
        ]

        if len(subtree) > 1:
            details = (
                f"Delete '{tag_name}' and its {len(subtree) - 1} child "
                f"tag(s)?"
            )
        else:
            details = f"Delete the tag '{tag_name}'?"
        if affected:
            details += f"\n\nIt will be removed from {len(affected)} project(s). The projects themselves stay on disk."
        if not frameless.question(
            self,
            "Delete tag?",
            details,
            yes="Delete",
            no="Cancel",
        ):
            return

        removed = tags.remove_tag(tree, tag_name)
        tags.save(tree)

        for entry in affected:
            proj = project_io.load(entry.path)
            if proj is None:
                continue
            proj.tags = [t for t in proj.tags if t not in removed]
            try:
                project_io.save(proj)
            except OSError:
                pass

        self.sidebar_tree.rebuild()
        # If we were viewing any of the removed tags, bounce to root.
        current = self.library_view.current_path()
        if any(n in removed for n in current):
            self.sidebar_tree.select_library_root()
        self.library_view.refresh()

    def _on_new_project(self) -> None:
        """Switch the content stack to the full-page New Project view."""
        # Refresh settings in case the user added a TD install right before clicking.
        self.settings = settings.load()
        self.new_project_view.reset(self.settings)
        self.stack.setCurrentWidget(self.new_project_view)

    def _on_new_project_created(self, result) -> None:
        # The tree reloads from tags.json so any newly-created tags the
        # user picked during project creation show up in the sidebar.
        self.sidebar_tree.rebuild()
        self.library_view.refresh()
        self.sidebar_tree.select_library_root()
        self.stack.setCurrentWidget(self.library_view)

        if result is not None and result.should_launch:
            # Reuse the library view's launch path so version-mismatch
            # dialogs + error reporting behave identically.
            if result.project.kind == "touchdesigner":
                toe_filename = result.project.toe_file
                self.library_view._on_launch(result.row_id, [toe_filename])
            else:
                # Non-TD: default run config.
                self.library_view._on_launch(result.row_id, ["default"])
        else:
            frameless.info(
                self,
                "Project created",
                "Project created. Click Launch on its card to open it.",
            )

    def _on_new_project_cancelled(self) -> None:
        self.stack.setCurrentWidget(self.library_view)

    def _on_add_existing(self) -> None:
        dialog = AddExistingDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.sidebar_tree.rebuild()
            self.library_view.refresh()
            self.sidebar_tree.select_library_root()

    def _on_settings_changed(self) -> None:
        self.settings = self.settings_view.current_settings

    # --- Edit Details navigation ---

    def _on_open_edit_details(self, row_id: int) -> None:
        """Switch the content stack to the full-page Edit Details view."""
        entry = library.get(row_id)
        if entry is None:
            return
        self.edit_details_view.set_entry(entry)
        self.stack.setCurrentWidget(self.edit_details_view)

    # --- Output dialog ---

    def _on_view_output(self, row_id: int) -> None:
        """Open or raise the modeless OutputView for `row_id`.

        One dialog per project. Reusing the existing instance preserves
        the user's scroll position and any unsaved selection state when
        they close-and-reopen the same project's output. Closed dialogs
        are removed from the dict via the destroyed signal so the next
        click constructs a fresh one (a `closeEvent` would also work
        but `destroyed` survives close-by-deleteLater paths).
        """
        from .output_view import OutputView  # lazy import — Qt-heavy module

        entry = library.get(row_id)
        if entry is None:
            return
        existing = self._output_views.get(row_id)
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        project_name = (
            entry.project.name if entry.project is not None else entry.path.name
        )
        dlg = OutputView(project_name, row_id, parent=self)
        self._output_views[row_id] = dlg
        dlg.destroyed.connect(
            lambda _obj=None, rid=row_id: self._output_views.pop(rid, None)
        )
        dlg.show()

    def _on_edit_details_saved(self, _row_id: int) -> None:
        # Refresh the library to pick up name/tag/variable changes, then
        # bounce back to the library view.
        self.library_view.refresh()
        self.stack.setCurrentWidget(self.library_view)

    def _on_edit_details_cancelled(self) -> None:
        self.stack.setCurrentWidget(self.library_view)

    def _on_splitter_moved(self, _pos: int, _index: int) -> None:
        """Debounced persistence of the sidebar width.

        ``splitterMoved`` fires continuously while the handle is being
        dragged, so writing on every tick would spam ``settings.json``.
        A single-shot timer collapses bursts into one save.
        """
        if not hasattr(self, "_splitter_save_timer"):
            self._splitter_save_timer = QTimer(self)
            self._splitter_save_timer.setSingleShot(True)
            self._splitter_save_timer.timeout.connect(self._save_sidebar_width)
        self._splitter_save_timer.start(400)

    def _save_sidebar_width(self) -> None:
        sizes = self.body_splitter.sizes()
        if not sizes:
            return
        width = int(sizes[0])
        if width <= 0 or width == self.settings.sidebar_width:
            return
        self.settings.sidebar_width = width
        settings.save(self.settings)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        # Flush any pending debounced save so the last drag position
        # always persists, even if the user quits immediately after.
        self._save_sidebar_width()
        super().closeEvent(event)

    # --- TouchDesigner preferences -----------------------------------------

    def _run_startup_prompts(self) -> None:
        try:
            self._report_last_update()
        except Exception:  # noqa: BLE001 — a status message must never block startup
            pass
        try:
            self._maybe_prompt_td_prefs()
        except Exception:  # noqa: BLE001 — a prompt failure must not cost the update check
            pass
        # Defer the update check a little so the first paint completes
        # before the network call fires.
        QTimer.singleShot(_UPDATE_STARTUP_DELAY_MS, self._maybe_check_updates_on_startup)

    def _maybe_prompt_td_prefs(self) -> None:
        """Ask once which recommended TD preferences ALPACKA should manage.

        Shown only when TD is installed and some recommended key is still
        undecided — so a later ALPACKA version that adds a key asks about
        just that one.
        """
        if not self.settings.td_installs:
            return
        majors = self.settings_view.td_majors()
        path = td_prefs.pref_path(majors[0])
        if path is None:
            return
        state = td_prefs.load_state()
        specs = td_prefs.undecided_specs(state)
        if not specs:
            return
        running = process_tracker.any_td_running()
        dialog = TDPrefsPromptDialog(
            specs,
            state,
            td_prefs.read_values(path),
            path,
            td_running=running,
            parent=self,
        )
        dialog.exec()
        if dialog.decision == "apply":
            accept, decline = dialog.table.pending_changes()
            td_prefs.decide(accept, decline)
            for major in majors:
                td_prefs.reconcile(major, td_running=running, source="ui")
        elif dialog.decision == "ignore":
            td_prefs.decide({}, [s.key for s in specs])
        # decision None (closed with ✕): no record, so it asks again next start.
        self.settings_view.refresh_td_prefs()

    def _on_td_prefs_result(self, result: "td_prefs.Result") -> None:
        """Keep Settings current and tell the user about launch-time changes."""
        try:
            self.settings_view.refresh_td_prefs(keep_edits=True)
        except Exception:  # noqa: BLE001
            pass
        if result.source != "launch":
            return
        # Every launch-time write is reported. A launch that has to wait
        # (another TD is running) is only reported when something was
        # changed outside ALPACKA, so multi-TD machines don't nag.
        if result.status == "error" or (
            result.changes and (result.status == "applied" or result.drift)
        ):
            self.notice_banner.show_message(td_prefs.describe_result(result))

    def _on_launch_held(self, names: list[str]) -> None:
        listed = ", ".join(names)
        self.notice_banner.show_message(
            f"Not started until you review what it runs: {listed}. "
            "Click Launch on the project to see its settings and approve them.",
            settings_link=False,
        )
        try:
            self.library_view.refresh()
        except Exception:  # noqa: BLE001
            pass

    def _open_settings(self) -> None:
        self.sidebar_tree.select_settings()

    # --- Update check ------------------------------------------------------

    def _maybe_check_updates_on_startup(self) -> None:
        """Fire a GitHub Releases check if the throttle has elapsed."""
        if not self.settings.auto_update_check:
            return
        now = time.time()
        if now - self.settings.last_update_check < _UPDATE_THROTTLE_SEC:
            return
        updater.start_check(self._on_startup_update_result)

    def _on_startup_update_result(self, info: "updater.UpdateInfo | None") -> None:
        """Record the check timestamp and, if a newer version exists and
        the user hasn't explicitly skipped it, show the update prompt."""
        self.settings.last_update_check = time.time()
        settings.save(self.settings)
        self.settings_view.set_latest_update(info)
        if info is None:
            return
        if info.version == self.settings.skipped_version:
            return
        self._prompt_update(info)

    def _prompt_update(self, info: "updater.UpdateInfo") -> None:
        if updater.can_install_in_place():
            self._prompt_install_update(info)
            return
        message = (
            f"ALPACKA {info.version} is available (you're on {__version__}).\n\n"
            "Open the release page to download the installer?"
        )
        choice = frameless.choose(
            self,
            "Update available",
            message,
            buttons=["Download", "Remind me later", "Skip this version"],
            primary_index=0,
        )
        if choice == 0:
            QDesktopServices.openUrl(QUrl(updater.safe_release_url(info.release_page)))
        elif choice == 2:
            self._skip_version(info)
        # choice == 1 (Remind me later) or -1 (dialog closed): no-op — the
        # 24h throttle re-prompts tomorrow.

    def _prompt_install_update(self, info: "updater.UpdateInfo") -> None:
        """The installed Windows build: offer to update in place."""
        message = (
            f"ALPACKA {info.version} is available (you're on {__version__}).\n\n"
            "ALPACKA will download it, close, install it and open again. "
            "Projects running in their own windows keep running."
        )
        while True:
            choice = frameless.choose(
                self,
                "Update available",
                message,
                buttons=["Update and restart", "Release notes", "Remind me later", "Skip this version"],
                primary_index=0,
            )
            if choice != 1:
                break
            QDesktopServices.openUrl(QUrl(updater.safe_release_url(info.release_page)))
        if choice == 0:
            update_dialog.run_update(self, info)
        elif choice == 3:
            self._skip_version(info)
        # choice == 2 (Remind me later) or -1 (dialog closed): no-op — the
        # 24h throttle re-prompts tomorrow.

    def _skip_version(self, info: "updater.UpdateInfo") -> None:
        self.settings.skipped_version = info.version
        settings.save(self.settings)
        self.settings_view.refresh_updates_status()

    def _report_last_update(self) -> None:
        """Say how an update started from ALPACKA went, once, at the next start."""
        result = updater.take_pending_result()
        if result is None:
            return
        if result.updated:
            text = f"Updated to ALPACKA {__version__}."
        else:
            text = (
                f"The update to ALPACKA {result.version} didn't finish, so this is "
                f"still {__version__}. The installer's log is at {result.log}"
            )
        self.notice_banner.show_message(text, settings_link=not result.updated)

    # --- Frameless-window plumbing -----------------------------------------

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.WindowStateChange:
            self.title_bar.update_maximize_icon()
        super().changeEvent(event)

    def nativeEvent(self, event_type, message):  # type: ignore[override]
        """On Windows, return WM_NCHITTEST codes for edge resize + Aero snap.

        Any part of the client area that isn't on an edge returns
        ``(False, 0)`` so Qt continues handling mouse events normally
        (including drag-to-move on the title bar, which we do via
        title_bar.py). We always return a tuple — delegating to
        ``super().nativeEvent`` can yield a bare bool on some PySide6
        builds, which fails Qt's expected ``(handled, result)`` contract
        and shows up as a Shiboken copy-convert error.
        """
        if sys.platform != "win32":
            return False, 0

        if event_type not in (b"windows_generic_MSG", "windows_generic_MSG"):
            return False, 0

        try:
            import ctypes
            from ctypes import wintypes

            msg = ctypes.cast(
                int(message), ctypes.POINTER(wintypes.MSG)
            ).contents
        except (OSError, ValueError):
            return False, 0

        WM_NCHITTEST = 0x0084
        if msg.message != WM_NCHITTEST:
            return False, 0

        if self.isMaximized() or self.isFullScreen():
            return False, 0

        # Extract (x, y) screen coords from lParam (low + high WORDs, signed).
        lparam = msg.lParam & 0xFFFFFFFF
        x = lparam & 0xFFFF
        y = (lparam >> 16) & 0xFFFF
        if x >= 0x8000:
            x -= 0x10000
        if y >= 0x8000:
            y -= 0x10000

        # WM_NCHITTEST lParam is in physical device pixels; frameGeometry() is
        # in logical (device-independent) pixels. On a scaled display (e.g.
        # 150% → dpr 1.5) they differ by the device pixel ratio, so comparing
        # them directly misclassifies a growing slice of the right/bottom of
        # the window as a resize border, stealing hover + clicks from the
        # widgets under it. Convert the hit point to logical pixels first.
        dpr = self.devicePixelRatioF() or 1.0
        frame = self.frameGeometry()
        local_x = x / dpr - frame.left()
        local_y = y / dpr - frame.top()
        w = frame.width()
        h = frame.height()

        margin = _RESIZE_BORDER
        on_left = local_x < margin
        on_right = local_x > w - margin
        on_top = local_y < margin
        on_bottom = local_y > h - margin

        HTLEFT, HTRIGHT, HTTOP, HTBOTTOM = 10, 11, 12, 15
        HTTOPLEFT, HTTOPRIGHT, HTBOTTOMLEFT, HTBOTTOMRIGHT = 13, 14, 16, 17

        if on_top and on_left:
            return True, HTTOPLEFT
        if on_top and on_right:
            return True, HTTOPRIGHT
        if on_bottom and on_left:
            return True, HTBOTTOMLEFT
        if on_bottom and on_right:
            return True, HTBOTTOMRIGHT
        if on_top:
            return True, HTTOP
        if on_bottom:
            return True, HTBOTTOM
        if on_left:
            return True, HTLEFT
        if on_right:
            return True, HTRIGHT

        # Not on an edge — defer to normal Qt processing.
        return False, 0
