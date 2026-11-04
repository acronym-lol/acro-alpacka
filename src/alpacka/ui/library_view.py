"""Library view — breadcrumb, tag tiles, and project card grid.

The content area behaves like a file browser:
- Root (``current_path = []``) shows every root-level tag as a drill-down
  tile, followed by an "All Projects" section containing every project.
- Inside a tag shows that tag's direct children as tiles, then the
  projects whose ``tags`` list contains the current leaf (exact match —
  no descendant rollup; users navigate deeper explicitly).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPaintEvent, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import (
    approval,
    crash_monitor,
    external,
    launch,
    library,
    process_tracker,
    settings,
    tags,
)
from . import dnd, frameless
from .approval_dialog import RunApprovalDialog
from .flow_layout import FlowLayout
from .project_card import ProjectCard
from .tag_icon import tag_icon


class _GridBackground(QWidget):
    """QWidget that paints a subtle grid behind its children.

    Used as the scroll area's inner widget AND as the wrapper for the
    empty-state label so the grid shows up in both.

    The bg uses gray-05 (`#141312`) — the darkest shade in the theme's
    warm-gray ramp — so the library reads as a "recessed well" under
    the lighter action bar. Grid lines use gray-15 so they stay just
    visible without dominating.
    """

    GRID_SPACING = 32
    GRID_COLOR = QColor("#262422")  # gray-15 — faint lines on the dark well
    BG_COLOR = QColor("#141312")    # gray-05 — darkest warm gray

    def __init__(self, object_name: str = "") -> None:
        super().__init__()
        if object_name:
            self.setObjectName(object_name)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.BG_COLOR)
        pen = QPen(self.GRID_COLOR)
        pen.setWidth(1)
        painter.setPen(pen)

        w = self.width()
        h = self.height()
        for x in range(0, w, self.GRID_SPACING):
            painter.drawLine(x, 0, x, h)
        for y in range(0, h, self.GRID_SPACING):
            painter.drawLine(0, y, w, y)
        painter.end()
        super().paintEvent(event)
from .td_install_dialog import (
    InstallProgressDialog,
    MismatchChoice,
    VersionMismatchDialog,
)


_ICONS_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"

# Tag tiles live on their own flow row in the content area. They size
# to their label width (no fixed grid) but share a single height so a
# row reads as uniform. Icon is significantly larger than the chip on
# a card, since a tile's whole job is to read as a tag.
TILE_HEIGHT = 80
TILE_ICON_SIZE = 32
TILE_MIN_WIDTH = 110


class TagTile(dnd.DragSource, QPushButton):
    """A drill-down tile in the content area.

    Rendered where project cards would normally sit; clicking navigates
    the library deeper into the tag tree. Uses ``QPushButton`` so we
    inherit hover / press states + the app-level pointer cursor filter.
    The tag.svg icon sits to the left of the two-line label (tag name
    on top, counts below).

    Doubles as a drag source (own tag) and drop target (tags for
    nesting, projects for tag assignment). Emits signals the enclosing
    ``LibraryView`` routes to ``tags.py`` / the library.
    """

    tag_drop_requested = Signal(str, str)      # source tag, target tag
    project_drop_requested = Signal(int, str)  # row_id, target tag

    DRAG_PREVIEW_WIDTH = 200

    def _drag_mime(self):
        return dnd.tag_mime(self.tag_name)

    def _restore_effect(self) -> None:
        self._apply_shadow()

    def __init__(
        self,
        tag_name: str,
        project_count: int,
        child_count: int,
        *,
        color: str = "",
    ) -> None:
        super().__init__()
        self.tag_name = tag_name
        self.setObjectName("tagTile")
        self.setFixedHeight(TILE_HEIGHT)
        self.setMinimumWidth(TILE_MIN_WIDTH)
        # Preferred width so the tile sizes to its own sizeHint instead
        # of being stretched across the flow row like a grid cell.
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self.setFlat(False)
        # Tint the tag icon by the tag's color; empty color falls back
        # to the default STEEL BLUE inside ``tag_icon``.
        self.setIcon(tag_icon(color, size=TILE_ICON_SIZE))
        self.setIconSize(QSize(TILE_ICON_SIZE, TILE_ICON_SIZE))
        self.setText(
            f"{tag_name}\n{self._format_label(project_count, child_count)}"
        )
        self.setAcceptDrops(True)
        self._init_drag_source()

        # Same drop-shadow treatment as project cards so tiles read as
        # sibling surfaces on the grid well — just a different hue.
        self._apply_shadow()

    def _make_shadow(self) -> QGraphicsDropShadowEffect:
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 110))
        return shadow

    def _apply_shadow(self) -> None:
        self.setGraphicsEffect(self._make_shadow())

    def sizeHint(self) -> QSize:  # type: ignore[override]
        # QPushButton's natural sizeHint uses its natural single-line
        # height, which FlowLayout would then treat as the row height
        # (causing overlap when the widget actually paints at the
        # fixed 80px). Clamp explicitly so FlowLayout uses the real
        # rendered size.
        base = super().sizeHint()
        return QSize(max(base.width(), TILE_MIN_WIDTH), TILE_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(TILE_MIN_WIDTH, TILE_HEIGHT)

    @staticmethod
    def _format_label(project_count: int, child_count: int) -> str:
        parts = []
        if child_count:
            parts.append(f"{child_count} sub")
        parts.append(f"{project_count} project{'' if project_count == 1 else 's'}")
        return ", ".join(parts)

    # --- Drop target ---------------------------------------------------

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        mime = event.mimeData()
        if mime.hasFormat(dnd.MIME_TAG) or mime.hasFormat(dnd.MIME_PROJECT):
            # Reject dropping a tag onto itself — no cycle benefit.
            if mime.hasFormat(dnd.MIME_TAG):
                src = dnd.extract_tag(mime)
                if src == self.tag_name:
                    event.ignore()
                    return
            event.acceptProposedAction()
            self.setProperty("dropActive", True)
            self.style().unpolish(self)
            self.style().polish(self)
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        mime = event.mimeData()
        if mime.hasFormat(dnd.MIME_TAG) or mime.hasFormat(dnd.MIME_PROJECT):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._clear_drop_active()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        self._clear_drop_active()
        mime = event.mimeData()

        project_id = dnd.extract_project(mime)
        if project_id is not None:
            event.acceptProposedAction()
            self.project_drop_requested.emit(project_id, self.tag_name)
            return

        source_tag = dnd.extract_tag(mime)
        if source_tag is not None and source_tag != self.tag_name:
            event.acceptProposedAction()
            self.tag_drop_requested.emit(source_tag, self.tag_name)
            return

        event.ignore()

    def _clear_drop_active(self) -> None:
        self.setProperty("dropActive", False)
        self.style().unpolish(self)
        self.style().polish(self)


class LibraryView(QWidget):
    """Emits `launched` when a project is successfully launched (row_id).

    Also emits ``navigate_requested`` with the new tag path whenever the
    user clicks a tag tile or a breadcrumb segment — the main window
    uses that to keep the sidebar tree selection in sync.
    """

    launched = Signal(int)
    navigate_requested = Signal(list)  # list[str] — root-to-leaf tag path
    tags_store_changed = Signal()      # tag tree mutated (new chips/tags)
    new_project_requested = Signal()
    add_existing_requested = Signal()
    # Bubbled up from card → MainWindow swaps the content stack to the
    # full-page Edit Details view rather than opening a modal here.
    edit_details_requested = Signal(int)  # row_id
    # Bubbled up from card → MainWindow opens (or raises) the modeless
    # OutputView dialog for that project. Tracked at the MainWindow
    # level so the dialog persists across library refreshes that
    # rebuild the cards.
    view_output_requested = Signal(int)  # row_id

    # Each card's minimum visual width. The grid wraps at viewport width
    # to avoid triggering a horizontal scrollbar.
    MIN_CARD_WIDTH = 280
    GRID_SPACING = 12
    GRID_MARGIN = 12
    MAX_COLUMNS = 5

    SORT_OPTIONS = [
        ("Recent", "recent"),
        ("Name", "name"),
        ("Added", "added"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._entries: list[library.LibraryEntry] = []
        self._tag_roots: list[tags.TagNode] = []
        self._current_path: list[str] = []
        self._cards_per_row = 3
        # The grid built in __init__ runs before the widget is shown, so
        # `viewport().width()` returns 0 and we fall back to the default
        # column count above. On first show, redo the layout once with the
        # real viewport width so cards size correctly without needing a
        # resize / interaction to trigger a relayout.
        self._initial_layout_done = False
        # Card density is persisted in settings; load the current value.
        self._compact = settings.load().card_density == "compact"
        # row_id → ProjectCard for O(1) partial updates instead of
        # rebuilding the whole grid on per-card edits (pin / toe / tag).
        self._cards_by_row_id: dict[int, ProjectCard] = {}
        # Tag-color lookup populated on every ``refresh``. Started
        # empty so the first ``_build_ui`` doesn't try to read it.
        self._tag_colors: dict[str, str] = {}
        self._build_ui()

        # Poll the process tracker so cards update within a few seconds
        # of TD exiting externally. On sweep, only rebuild if a PID
        # actually disappeared — avoids gratuitous grid rebuilds.
        self._process_poll = QTimer(self)
        self._process_poll.setInterval(6000)
        self._process_poll.timeout.connect(self._poll_running_processes)
        self._process_poll.start()

        self.refresh()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_current_path(self, path: list[str]) -> None:
        """Navigate to a tag path (empty list = library root)."""
        new_path = [str(p) for p in path]
        if new_path == self._current_path:
            return
        self._current_path = new_path
        self._rebuild_grid()

    def _on_density_toggled(self, checked: bool) -> None:
        self._compact = bool(checked)
        self._update_density_btn_text()
        current = settings.load()
        current.card_density = "compact" if self._compact else "expanded"
        settings.save(current)
        self._rebuild_grid()

    def _update_density_btn_text(self) -> None:
        # Label shows the mode the click *would produce*, following the
        # "verb / next state" convention most density toggles use.
        self._density_btn.setText("Expanded" if self._compact else "Compact")
        self._density_btn.setToolTip(
            "Click to switch to expanded cards (with thumbnails)."
            if self._compact
            else "Click to switch to compact cards (no thumbnails)."
        )

    def current_path(self) -> list[str]:
        return list(self._current_path)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Zero outer margins so the recessed gray-05 well runs flush to the
        # window edges (matching the sidebar height). The toolbar carries
        # its own inset padding instead.
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Toolbar splits across two rows: primary identity + actions up
        # top, view controls (search / sort / density / refresh) below.
        # Two rows keep each row scannable and leave the heading
        # uncrowded when the sidebar narrows the content width.
        toolbar_widget = QWidget()
        toolbar_widget.setObjectName("libraryToolbar")
        toolbar = QVBoxLayout(toolbar_widget)
        toolbar.setContentsMargins(24, 14, 24, 10)
        toolbar.setSpacing(8)

        # Row 1 — heading + primary actions.
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        heading = QLabel("Library")
        heading.setObjectName("sectionHeading")
        row1.addWidget(heading)
        row1.addStretch(1)

        new_btn = QPushButton("New Project")
        new_btn.setObjectName("primaryButton")
        new_btn.clicked.connect(
            lambda _=False: self.new_project_requested.emit()
        )
        row1.addWidget(new_btn)

        add_btn = QPushButton("Add Existing")
        add_btn.clicked.connect(
            lambda _=False: self.add_existing_requested.emit()
        )
        row1.addWidget(add_btn)

        toolbar.addLayout(row1)

        # Row 2 — view controls.
        row2 = QHBoxLayout()
        row2.setSpacing(8)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search projects...")
        self.search_edit.textChanged.connect(self._rebuild_grid)
        row2.addWidget(self.search_edit, stretch=1)

        self.sort_combo = QComboBox()
        for label, key in self.SORT_OPTIONS:
            self.sort_combo.addItem(label, userData=key)
        self.sort_combo.currentIndexChanged.connect(self._rebuild_grid)
        row2.addWidget(self.sort_combo)

        # Density toggle — flips card height + thumbnail/description
        # visibility. Checked state == compact.
        self._density_btn = QPushButton()
        self._density_btn.setObjectName("densityToggle")
        self._density_btn.setCheckable(True)
        self._density_btn.setChecked(self._compact)
        self._density_btn.clicked.connect(self._on_density_toggled)
        self._update_density_btn_text()
        row2.addWidget(self._density_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        row2.addWidget(refresh_btn)

        toolbar.addLayout(row2)

        root.addWidget(toolbar_widget)

        # Scrollable card area. Vertical scroll only; the card grid
        # wraps responsively to the viewport so nothing can overflow
        # horizontally. The inner widget is rebuilt on every refresh
        # (Qt takes ownership via setWidget and deletes the previous
        # one), so we never cache grid/container instance refs.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        root.addWidget(self.scroll, stretch=1)

    def refresh(self) -> None:
        # Preserve the user's scroll position across the rebuild.
        # ``_rebuild_grid`` replaces the scroll area's inner widget,
        # which would otherwise reset the scrollbar to the top on every
        # refresh — jarring for small edits like toggling a tag chip,
        # pinning, or flipping a .toe selection.
        scrollbar = self.scroll.verticalScrollBar()
        saved_scroll = scrollbar.value() if scrollbar is not None else 0

        library.refresh_stale()
        self._entries = library.list_entries()
        self._tag_roots = tags.load()
        # Shared tag-color map passed to each card so chips match the
        # tag's sidebar/tile color. The cards keep a live reference to
        # this dict so it reflects updates on partial rebuilds.
        self._tag_colors = {
            node.name: node.color for node in tags.walk(self._tag_roots)
        }
        # If we were inside a tag that no longer exists, bubble back up to root.
        if self._current_path and tags.find(self._tag_roots, self._current_path[-1]) is None:
            self._current_path = []
        self._rebuild_grid()

        # Defer the restore to the next event-loop tick so the new
        # container has finished its initial layout — the vertical
        # scrollbar's range isn't final until then.
        if scrollbar is not None:
            QTimer.singleShot(
                0, lambda: scrollbar.setValue(saved_scroll)
            )

    def _compute_cards_per_row(self) -> int:
        viewport_w = self.scroll.viewport().width()
        if viewport_w <= 0:
            return self._cards_per_row or 1

        usable = viewport_w - (self.GRID_MARGIN * 2)
        if usable <= 0:
            return 1

        per_col = self.MIN_CARD_WIDTH + self.GRID_SPACING
        # cols columns need: cols*MIN_CARD_WIDTH + (cols-1)*GRID_SPACING <= usable
        # Rearranging: cols <= (usable + GRID_SPACING) / per_col
        cols = max(1, (usable + self.GRID_SPACING) // per_col)
        return int(max(1, min(cols, self.MAX_COLUMNS)))

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        new_cols = self._compute_cards_per_row()
        if new_cols != self._cards_per_row:
            self._cards_per_row = new_cols
            self._rebuild_grid()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        # First show — viewport now has its real width. Recompute the
        # column count and rebuild if it differs from the placeholder we
        # used during __init__.
        if self._initial_layout_done:
            return
        self._initial_layout_done = True
        new_cols = self._compute_cards_per_row()
        if new_cols != self._cards_per_row:
            self._cards_per_row = new_cols
            self._rebuild_grid()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _rebuild_grid(self) -> None:
        # Recompute columns in case the viewport size has changed since
        # the last rebuild (e.g., first paint, nav switch).
        self._cards_per_row = self._compute_cards_per_row()

        filtered_all = self._filter_and_sort(self._entries)
        projects_here = self._projects_for_path(filtered_all)

        container = _GridBackground("libraryContainer")
        outer = QVBoxLayout(container)
        outer.setContentsMargins(
            self.GRID_MARGIN, self.GRID_MARGIN, self.GRID_MARGIN, self.GRID_MARGIN
        )
        outer.setSpacing(10)

        breadcrumb = self._render_breadcrumb()
        if breadcrumb is not None:
            outer.addWidget(breadcrumb)

        child_tiles = self._render_tag_tiles(filtered_all)
        if child_tiles is not None:
            outer.addWidget(child_tiles)

        outer.addWidget(self._render_projects_heading(projects_here))

        if not projects_here:
            outer.addWidget(self._render_empty_placeholder())
        else:
            outer.addWidget(self._render_project_grid(projects_here), stretch=1)

        outer.addStretch(1)

        self.scroll.setWidget(container)

    # --- Breadcrumb ----------------------------------------------------

    def _render_breadcrumb(self) -> QWidget | None:
        # At root the breadcrumb would just echo the "Library" heading
        # already present in the toolbar, so hide it entirely. Inside a
        # tag we still render it — the "Library" crumb is the user's
        # one-click way back to root.
        if not self._current_path:
            return None

        row = QWidget()
        row.setObjectName("breadcrumbRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(2, 2, 2, 6)
        layout.setSpacing(4)

        root_btn = self._make_breadcrumb_button("Library", [], is_leaf=False)
        layout.addWidget(root_btn)

        for i, name in enumerate(self._current_path):
            sep = QLabel("/")
            sep.setObjectName("breadcrumbSeparator")
            layout.addWidget(sep)
            is_leaf = i == len(self._current_path) - 1
            layout.addWidget(
                self._make_breadcrumb_button(
                    name, self._current_path[: i + 1], is_leaf=is_leaf
                )
            )

        layout.addStretch(1)
        return row

    def _make_breadcrumb_button(
        self, text: str, target_path: list[str], *, is_leaf: bool
    ) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("breadcrumbLeaf" if is_leaf else "breadcrumbLink")
        btn.setFlat(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if is_leaf:
            btn.setEnabled(False)
        else:
            btn.clicked.connect(
                lambda _checked=False, p=list(target_path): self._navigate_to(p)
            )
        return btn

    # --- Tag tiles -----------------------------------------------------

    def _render_tag_tiles(
        self, all_projects: list[library.LibraryEntry]
    ) -> QWidget | None:
        leaf = self._current_path[-1] if self._current_path else None
        children = tags.children_of(self._tag_roots, leaf)
        if not children:
            return None

        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Heading row — label + inline "+" button that adds a new child
        # of the current path (or a root tag when at the library root).
        heading_row = QWidget()
        heading_layout = QHBoxLayout(heading_row)
        heading_layout.setContentsMargins(0, 0, 0, 0)
        heading_layout.setSpacing(6)

        heading = QLabel("Tags" if not self._current_path else "Child tags")
        heading.setObjectName("tertiaryHeading")
        heading_layout.addWidget(heading)

        add_btn = QPushButton()
        add_btn.setObjectName("tagAddButton")
        add_btn.setFlat(True)
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setIcon(QIcon(str(_ICONS_DIR / "plus.svg")))
        add_btn.setIconSize(QSize(14, 14))
        add_btn.setFixedSize(20, 20)
        parent_for_new = (
            self._current_path[-1] if self._current_path else None
        )
        if parent_for_new:
            add_btn.setToolTip(f"Add a new tag under '{parent_for_new}'")
        else:
            add_btn.setToolTip("Add a new root tag")
        add_btn.clicked.connect(
            lambda _=False, p=parent_for_new: self.apply_tag_add(p)
        )
        heading_layout.addWidget(add_btn)
        heading_layout.addStretch(1)

        layout.addWidget(heading_row)

        # Flow row so tiles size to their content width and wrap when
        # they run out of room — decoupled from the project card grid.
        flow_host = QWidget()
        flow = FlowLayout(
            flow_host, margin=0, h_spacing=self.GRID_SPACING, v_spacing=self.GRID_SPACING
        )

        # Project-count uses the full (pre-tag-filter) entry list so the
        # count reflects the tag universe, not the current filter.
        for node in children:
            proj_count = sum(
                1 for e in all_projects if e.project and node.name in e.project.tags
            )
            tile = TagTile(
                tag_name=node.name,
                project_count=proj_count,
                child_count=len(node.children),
                color=node.color,
            )
            tile.clicked.connect(
                lambda _checked=False, n=node.name: self._navigate_to(
                    self._current_path + [n]
                )
            )
            tile.tag_drop_requested.connect(self._on_tag_dropped_on_tag)
            tile.project_drop_requested.connect(
                self._on_project_dropped_on_tag
            )
            flow.addWidget(tile)

        layout.addWidget(flow_host)
        return wrapper

    # --- Project grid --------------------------------------------------

    def _render_projects_heading(
        self, projects: list[library.LibraryEntry]
    ) -> QWidget:
        if not self._current_path:
            text = "All Projects"
        else:
            text = f"Projects tagged '{self._current_path[-1]}'"
        count_text = f" ({len(projects)})" if projects else ""

        heading = QLabel(text + count_text)
        heading.setObjectName("tertiaryHeading")
        return heading

    def _render_project_grid(
        self, entries: list[library.LibraryEntry]
    ) -> QWidget:
        container = QWidget()
        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(self.GRID_SPACING)
        grid.setVerticalSpacing(self.GRID_SPACING)

        # Rebuilding the grid → drop the previous card map so partial
        # updates don't chase widgets that are about to be deleted.
        self._cards_by_row_id = {}

        for i, entry in enumerate(entries):
            card = ProjectCard(
                entry, compact=self._compact, tag_colors=self._tag_colors
            )
            self._cards_by_row_id[entry.row_id] = card
            card.launch_requested.connect(self._on_launch)
            card.launch_new_requested.connect(self._on_launch_new)
            card.focus_requested.connect(self._on_focus)
            card.stop_requested.connect(self._on_stop)
            card.open_folder_requested.connect(self._on_open_folder)
            card.open_vscode_requested.connect(self._on_open_vscode)
            card.open_terminal_requested.connect(self._on_open_terminal)
            card.open_repo_requested.connect(self._on_open_repo)
            card.open_url_requested.connect(self._on_open_url)
            card.view_output_requested.connect(self._on_view_output)
            card.edit_details_requested.connect(self._on_edit_details)
            card.autostart_settings_requested.connect(
                self._on_autostart_settings
            )
            card.remove_requested.connect(self._on_remove)
            card.toggle_pin_requested.connect(self._on_toggle_pin)
            card.tags_changed.connect(self._on_tags_changed)
            card.default_toes_changed.connect(self._on_default_toes_changed)

            row = i // self._cards_per_row
            col = i % self._cards_per_row
            grid.addWidget(card, row, col)

        for col in range(self._cards_per_row):
            grid.setColumnStretch(col, 1)

        return container

    def _render_empty_placeholder(self) -> QWidget:
        if not self._current_path:
            text = (
                "No projects yet. Click New Project or Add Existing to start."
            )
        else:
            text = (
                f"No projects tagged '{self._current_path[-1]}' yet. "
                "Add the tag to a project via its ⋯ menu."
            )
        label = QLabel(text)
        label.setObjectName("emptyLibrary")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        return label

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _projects_for_path(
        self, entries: list[library.LibraryEntry]
    ) -> list[library.LibraryEntry]:
        if not self._current_path:
            return entries
        leaf = self._current_path[-1]
        return [
            e for e in entries
            if e.project and leaf in e.project.tags
        ]

    def _filter_and_sort(
        self, entries: list[library.LibraryEntry]
    ) -> list[library.LibraryEntry]:
        query = self.search_edit.text().strip().lower()
        filtered = [e for e in entries if _matches_query(e, query)] if query else list(entries)

        sort_key = self.sort_combo.currentData()
        if sort_key == "name":
            filtered.sort(key=lambda e: _display_name(e).lower())
        elif sort_key == "added":
            filtered.sort(key=lambda e: e.added_at, reverse=True)
        else:
            # Recent: pinned first, then most-recent timestamp first. Two
            # stable sort passes — last-applied key is the primary order,
            # so timestamp desc first, then pin asc.
            filtered.sort(
                key=lambda e: e.last_launched or e.added_at or "",
                reverse=True,
            )
            filtered.sort(key=lambda e: not e.pinned)
        return filtered

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _navigate_to(self, path: list[str]) -> None:
        self.set_current_path(path)
        self.navigate_requested.emit(list(path))

    # ------------------------------------------------------------------
    # Action handlers (unchanged from pre-refactor)
    # ------------------------------------------------------------------

    def _poll_running_processes(self) -> None:
        """Periodic sweep — drives both UI state and crash monitoring."""
        dead = process_tracker.sweep_dead()
        alive = process_tracker.list_all()
        crash_monitor.tick(dead=dead, alive=alive)
        if dead:
            self.refresh()

    def _on_focus(self, row_id: int, pid: int) -> None:
        if not process_tracker.focus_window(pid):
            # Couldn't raise the window — most commonly because it died
            # between card-render and click. Do a sweep + refresh so the
            # card flips back to Launch, then tell the user.
            removed = process_tracker.sweep_dead()
            if removed:
                self.refresh()
            else:
                frameless.info(
                    self,
                    "Could not focus",
                    "Couldn't bring the project's window to the foreground.",
                )

    def _on_stop(self, row_id: int, pid: int) -> None:
        # Count tracked processes for the project so the prompt can tell
        # the user whether they're killing one thing or several. The pid
        # arg is ignored — Stop is project-scoped (main + all deps) once
        # launch dependencies exist.
        try:
            records = process_tracker.active(process_tracker.list_for_project(row_id))
        except Exception:  # noqa: BLE001
            records = []
        n = len(records)
        if n <= 1:
            prompt = "Terminate the running process for this project?"
        else:
            prompt = (
                f"Terminate all {n} running processes for this project "
                f"(main + dependencies)?"
            )
        if not frameless.question(
            self,
            "Terminate processes?" if n > 1 else "Terminate process?",
            prompt,
            yes="Stop",
            no="Cancel",
        ):
            return
        # Tell the crash monitor this is a user-initiated stop so the
        # next dead-process detection doesn't trigger an auto-relaunch.
        crash_monitor.suppress_next_relaunch(row_id)
        process_tracker.terminate_project_processes(row_id)
        self.refresh()

    def _on_launch_new(self, row_id: int, targets: list) -> None:
        # Same pipeline as _on_launch but never short-circuits to focus.
        self._on_launch(row_id, targets)

    def _on_launch(self, row_id: int, targets: list) -> None:
        """Launch a project. For TD `targets` is a list of .toe filenames;
        for other kinds it's a list of run-config names (typically ["default"]).

        For TD, the pinned install is resolved once for the whole batch so
        the version-mismatch dialog only appears once even if the user
        ticked five files. For other kinds we dispatch through
        `launch.launch()` per run-config name — no TD install detection.
        """
        entry = library.get(row_id)
        if entry is None or not targets:
            return

        # Manual launch = fresh slate for crash monitoring — reset retry
        # counter and drop any pending auto-relaunch for this project.
        crash_monitor.reset(row_id)

        if entry.project is None:
            frameless.critical(
                self,
                "Could not launch",
                f"alpacka.yml missing or unreadable at {entry.path}",
            )
            return

        if not self._ensure_approved(entry):
            return

        if entry.project.kind != "touchdesigner":
            self._launch_run_configs(entry, [str(t) for t in targets])
            return

        from .. import td_install as ti
        installs = ti.detect()
        if not installs:
            frameless.critical(
                self,
                "Could not launch",
                "No TouchDesigner installation detected. Install TD to launch projects.",
            )
            return

        pinned = entry.project.td_version
        install = ti.find_by_version(installs, pinned) if pinned else None
        if install is None and pinned:
            install = self._resolve_version_mismatch_once(entry, pinned, installs)
            if install is None:
                return
        if install is None:
            install = installs[0]

        self._launch_batch(entry, install, list(targets))

    def _ensure_approved(self, entry: library.LibraryEntry) -> bool:
        """Show what the project runs and ask, unless it's already approved.

        Every launch path also checks this in `launch._spawn`; asking here
        first keeps a batch launch to one prompt.
        """
        if approval.is_approved(entry):
            return True
        dialog = RunApprovalDialog(entry.project, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        approval.approve(entry)
        self.refresh()
        return True

    def _launch_run_configs(
        self, entry: library.LibraryEntry, run_config_names: list[str]
    ) -> None:
        """Spawn a non-TD project using one or more named run configs."""
        failed: list[tuple[str, str]] = []
        for name in run_config_names:
            try:
                launch.launch(entry, run_config_name=name)
            except launch.LaunchError as exc:
                failed.append((name, str(exc)))

        if failed:
            lines = "\n".join(f"• {name}: {msg}" for name, msg in failed)
            frameless.critical(
                self,
                "Some launches failed",
                f"Could not launch the following run configs:\n\n{lines}",
            )

        self.launched.emit(entry.row_id)
        self.refresh()

    def _launch_batch(
        self,
        entry: library.LibraryEntry,
        install,
        toe_filenames: list[str],
    ) -> None:
        """Spawn one TD instance per selected .toe using `install`."""
        failed: list[tuple[str, str]] = []
        for toe in toe_filenames:
            try:
                launch.launch_toe(entry, toe, install=install)
            except launch.LaunchError as exc:
                failed.append((toe, str(exc)))

        if failed:
            lines = "\n".join(f"• {name}: {msg}" for name, msg in failed)
            frameless.critical(
                self,
                "Some launches failed",
                f"Could not launch the following files:\n\n{lines}",
            )

        self.launched.emit(entry.row_id)
        self.refresh()

    def _resolve_version_mismatch_once(
        self,
        entry: library.LibraryEntry,
        pinned: str,
        installs: list,
    ):
        """Show the version-mismatch dialog once for a batch launch.

        Returns a resolved TD install on success, or None if the user
        cancelled the dialog.
        """
        from .. import td_install as ti
        if not ti.is_valid_version(pinned):
            # The pin comes from alpacka.yml and ends up in a download URL
            # and file name; refuse anything that isn't a plain build number.
            frameless.critical(
                self,
                "Could not launch",
                f"This project pins TouchDesigner version “{pinned}”, which isn't "
                "a version number (like 2025.32460). Fix td_version in its "
                "alpacka.yml, or open the .toe in TouchDesigner and save it.",
            )
            return None
        closest = ti.newest_at_least(installs, pinned)
        dialog = VersionMismatchDialog(
            pinned=pinned, closest_newer=closest, parent=self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None

        result = dialog.result_choice
        if result.choice == MismatchChoice.LAUNCH_WITH_NEWER and result.install:
            return result.install

        if result.choice == MismatchChoice.INSTALL_PINNED:
            progress = InstallProgressDialog(version=pinned, parent=self)
            progress.start()
            if not progress.succeeded:
                return None
            detected = ti.detect()
            app_settings = settings.load()
            if detected:
                app_settings.td_installs = detected
                settings.save(app_settings)
            return ti.find_by_version(detected, pinned)

        return None

    def _on_open_folder(self, row_id: int) -> None:
        entry = library.get(row_id)
        if entry is None or not entry.path.exists():
            return
        try:
            external.open_folder(entry.path)
        except external.ExternalToolError as exc:
            frameless.critical(self, "Could not open folder", str(exc))

    def _on_open_vscode(self, row_id: int) -> None:
        entry = library.get(row_id)
        if entry is None or not entry.path.exists():
            return
        try:
            external.open_vscode(entry.path)
        except external.ExternalToolError as exc:
            frameless.critical(self, "Could not open in VS Code", str(exc))

    def _on_open_terminal(self, row_id: int) -> None:
        entry = library.get(row_id)
        if entry is None or not entry.path.exists():
            return
        try:
            external.open_terminal(entry.path)
        except external.ExternalToolError as exc:
            frameless.critical(self, "Could not open terminal", str(exc))

    def _on_open_repo(self, row_id: int, url: str) -> None:
        try:
            external.open_repo_url(url)
        except external.ExternalToolError as exc:
            frameless.critical(self, "Could not open repo URL", str(exc))

    def _on_open_url(self, row_id: int, url: str) -> None:
        """Open a kind-provided URL (e.g. nodejs http://localhost:{PORT})."""
        try:
            external.open_repo_url(url)  # same http(s) opener
        except external.ExternalToolError as exc:
            frameless.critical(self, "Could not open URL", str(exc))

    def _on_edit_details(self, row_id: int) -> None:
        # Hand off to the MainWindow, which switches the content stack
        # to the full-page Edit Details view.
        if library.get(row_id) is None:
            return
        self.edit_details_requested.emit(row_id)

    def _on_view_output(self, row_id: int) -> None:
        # MainWindow owns the dict[row_id, OutputView] so dialogs
        # persist across library refreshes that rebuild the cards.
        if library.get(row_id) is None:
            return
        self.view_output_requested.emit(row_id)

    def _on_autostart_settings(self, row_id: int) -> None:
        entry = library.get(row_id)
        if entry is None:
            return
        from .autostart_settings_dialog import AutostartSettingsDialog
        dialog = AutostartSettingsDialog(entry, parent=self)
        dialog.exec()
        if dialog.saved:
            self.refresh()

    def _on_remove(self, row_id: int) -> None:
        entry = library.get(row_id)
        if entry is None:
            return
        if frameless.question(
            self,
            "Remove from Library?",
            f"Remove '{_display_name(entry)}' from the library?\n\n"
            f"The folder on disk will not be touched.",
            yes="Remove",
            no="Cancel",
        ):
            library.remove(row_id)
            self.refresh()

    def _focus_pid_for_row(self, row_id: int) -> int | None:
        """Return the first running PID for a project, or None."""
        entry = library.get(row_id)
        if entry is None or not entry.running_processes:
            return None
        return entry.running_processes[0].pid

    def _on_toggle_pin(self, row_id: int) -> None:
        entry = library.get(row_id)
        if entry is None:
            return
        library.set_pinned(row_id, not entry.pinned)
        # Partial update — avoids the flicker + scroll-jump of a full
        # grid rebuild. Sort order only reconciles on the next full
        # refresh (nav change, search, etc.), which is the expected
        # trade: a just-pinned card staying in view is more useful than
        # watching it jump to the top.
        self._update_card(row_id)

    def _update_card(self, row_id: int) -> None:
        """Refresh a single card in place with the latest library state.

        Falls back to a full refresh when the project has been removed
        or the card isn't currently rendered (e.g. hidden by a tag
        filter) — at that point the grid needs to reconcile anyway.
        """
        entry = library.get(row_id)
        card = self._cards_by_row_id.get(row_id)
        if entry is None or card is None:
            self.refresh()
            return
        card.update_entry(entry)

    def _on_tag_dropped_on_tag(self, source_tag: str, target_tag: str) -> None:
        """Nest `source_tag` under `target_tag` (content-area tile drop)."""
        self.apply_tag_move(source_tag, target_tag)

    def _on_project_dropped_on_tag(self, row_id: int, target_tag: str) -> None:
        """Drop a project card onto a tag tile — tag add / move."""
        self.apply_project_tag_drop(row_id, target_tag)

    def apply_tag_add(self, parent_name: str | None) -> None:
        """Open the tag-add dialog and persist on save.

        ``parent_name`` chooses where the new tag lands — ``None``
        drops it at the tree root, otherwise under the named tag.
        """
        from .tag_edit_dialog import TagEditDialog
        result = TagEditDialog.add(self, parent_hint=parent_name)
        if result is None:
            return
        name, color = result
        tree = tags.load()
        try:
            tags.add_tag(tree, name, parent_name=parent_name)
            if color:
                tags.set_tag_color(tree, name, color)
        except tags.TagError as exc:
            frameless.warning(self, "Could not add tag", str(exc))
            return
        tags.save(tree)
        self.refresh()
        self.tags_store_changed.emit()

    def apply_tag_move(
        self, tag_name: str, new_parent: str | None
    ) -> None:
        """Move `tag_name` under `new_parent` (None = tree root)."""
        tree = tags.load()
        try:
            tags.move_tag(tree, tag_name, new_parent)
        except tags.TagError:
            return
        tags.save(tree)
        self.refresh()
        self.tags_store_changed.emit()

    def apply_project_tag_drop(
        self, row_id: int, target_tag: str | None
    ) -> None:
        """Handle a project-card drop onto a tag (or Library root).

        At root the operation is additive (adds ``target_tag`` to the
        project). Inside a tag the current tag is removed first so the
        card visually "moves" from one tag view to another.
        """
        from .. import project as project_io
        entry = library.get(row_id)
        if entry is None or entry.project is None:
            return

        current_tag = self._current_path[-1] if self._current_path else None

        existing = list(entry.project.tags)
        new_set = set(existing)

        if current_tag is not None:
            new_set.discard(current_tag)
        if target_tag is not None:
            new_set.add(target_tag)

        if new_set == set(existing):
            return  # net zero change

        # Preserve original order as best we can.
        final = [t for t in existing if t in new_set]
        for t in new_set:
            if t not in final:
                final.append(t)

        proj = project_io.load(entry.path)
        if proj is None:
            frameless.critical(
                self,
                "Could not update tags",
                f"alpacka.yml is missing or unreadable at {entry.path}",
            )
            return
        proj.tags = final
        try:
            project_io.save(proj)
        except OSError as exc:
            frameless.critical(self, "Could not update tags", str(exc))
            return

        # Ensure target_tag exists in the tree; the sidebar rebuild
        # picks it up via the tags_store_changed signal below.
        if target_tag is not None:
            tree = tags.load()
            if tags.ensure_tags_exist(tree, [target_tag]):
                tags.save(tree)

        self.refresh()
        self.tags_store_changed.emit()

    def _on_default_toes_changed(self, row_id: int, new_toes: list) -> None:
        """Persist an inline toe-toggle from the card menu."""
        from .. import project as project_io
        entry = library.get(row_id)
        if entry is None or entry.project is None:
            return
        proj = project_io.load(entry.path)
        if proj is None:
            frameless.critical(
                self,
                "Could not save .toe selection",
                f"alpacka.yml is missing or unreadable at {entry.path}",
            )
            return
        proj.default_toes = [str(t) for t in new_toes]
        try:
            project_io.save(proj)
        except OSError as exc:
            frameless.critical(self, "Could not save .toe selection", str(exc))
            return
        # Toe changes never affect filter visibility or sort order, so
        # a partial update is always safe here.
        self._update_card(row_id)

    def _on_tags_changed(self, row_id: int, new_tags: list) -> None:
        """Persist card-originated tag edits + refresh the tree + grid."""
        from .. import project as project_io
        entry = library.get(row_id)
        if entry is None or entry.project is None:
            return
        # Reload from disk so any concurrent writes (e.g. AcroCore touching
        # `last_opened`) aren't clobbered by the merge-save.
        proj = project_io.load(entry.path)
        if proj is None:
            frameless.critical(
                self,
                "Could not save tags",
                f"alpacka.yml is missing or unreadable at {entry.path}",
            )
            return
        proj.tags = [str(t) for t in new_tags]
        try:
            project_io.save(proj)
        except OSError as exc:
            frameless.critical(self, "Could not save tags", str(exc))
            return
        # The picker already wrote any newly-created tags to tags.json,
        # so a rebuild of the sidebar + grid is all that's left.
        self.refresh()
        self.tags_store_changed.emit()


def _matches_query(entry: library.LibraryEntry, query: str) -> bool:
    haystack = [str(entry.path).lower()]
    if entry.project:
        haystack.append(entry.project.name.lower())
        haystack.append(entry.project.description.lower())
        haystack.extend(t.lower() for t in entry.project.tags)
        haystack.append(entry.project.template.lower())
    return any(query in h for h in haystack)


def _display_name(entry: library.LibraryEntry) -> str:
    if entry.project and entry.project.name:
        return entry.project.name
    return entry.path.name
