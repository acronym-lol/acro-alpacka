"""Sidebar tree with Library / Templates / Settings + nested tags.

The tree is authoritative about navigation; the MainWindow wires its
``selection_changed`` signal to the stacked content and to the library
view's ``set_current_path``. Tag nodes support internal drag-and-drop
reordering — drops that would create a cycle, or that land on the
non-Library top-levels, are rejected.

Qt's default InternalMove DnD moves items in place, but we prefer to
treat ``tags.json`` as the source of truth: validate the move against
``tags.move_tag`` (cycle + duplicate guards live there), persist, then
rebuild the tree from the file.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDropEvent, QIcon, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from .. import tags
from . import dnd
from .tag_icon import tag_icon


_ICONS_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"


class _TagActionsDelegate(QStyledItemDelegate):
    """Paints add / edit / delete icons on the right of hovered rows.

    Tag rows get all three icons ( + edit trash ) on hover. The Library
    root row gets only the + icon so users can drop new tags at root
    straight from the tree. Hover state updates via
    ``SidebarTree.mouseMoveEvent`` / ``leaveEvent``; click hit-testing
    lives on the tree, consulting ``hit_test`` before falling through
    to Qt's default selection/drag logic.
    """

    ICON_SIZE = 14
    ICON_GAP = 4
    RIGHT_PAD = 6

    def __init__(
        self,
        plus_icon: QIcon,
        edit_icon: QIcon,
        trash_icon: QIcon,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._plus_icon = plus_icon
        self._edit_icon = edit_icon
        self._trash_icon = trash_icon
        self._hover_index = QModelIndex()
        # Cache the painted rects so ``hit_test`` can map a mouse-press
        # back to an action. Keyed by row kind + name so the Library
        # row (which has no name) doesn't collide with tag rows.
        self._hit_rects: dict[str, dict[str, QRect]] = {}

    # --- Hover state --------------------------------------------------

    def set_hover_index(self, index: QModelIndex) -> None:
        if index == self._hover_index:
            return
        self._hover_index = QModelIndex(index)
        view = self.parent()
        if isinstance(view, QAbstractItemView):
            view.viewport().update()

    # --- Painting -----------------------------------------------------

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        # Drop the focus-rect state bit before base-class paint — Qt's
        # default item delegate draws a dotted outline ("focus rect")
        # when the item has keyboard focus, which reads as a bright
        # white marker on our dark theme and fights the QSS selection
        # styling. We never need the outline since selection already
        # has its own visual treatment.
        option_no_focus = QStyleOptionViewItem(option)
        option_no_focus.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, option_no_focus, index)

        if not index.isValid() or index != self._hover_index:
            return
        kind = index.data(_ROLE_KIND)
        if kind not in (_KIND_TAG, _KIND_LIBRARY):
            return

        rect = option.rect
        y = rect.top() + (rect.height() - self.ICON_SIZE) // 2
        right = rect.right() - self.RIGHT_PAD

        rects: dict[str, QRect] = {}

        if kind == _KIND_TAG:
            trash_rect = QRect(
                right - self.ICON_SIZE, y, self.ICON_SIZE, self.ICON_SIZE
            )
            edit_rect = QRect(
                trash_rect.left() - self.ICON_GAP - self.ICON_SIZE,
                y,
                self.ICON_SIZE,
                self.ICON_SIZE,
            )
            plus_rect = QRect(
                edit_rect.left() - self.ICON_GAP - self.ICON_SIZE,
                y,
                self.ICON_SIZE,
                self.ICON_SIZE,
            )
            self._plus_icon.paint(painter, plus_rect)
            self._edit_icon.paint(painter, edit_rect)
            self._trash_icon.paint(painter, trash_rect)
            rects["add"] = plus_rect
            rects["edit"] = edit_rect
            rects["delete"] = trash_rect
            key = f"tag:{index.data(Qt.ItemDataRole.DisplayRole)}"
        else:  # Library root
            plus_rect = QRect(
                right - self.ICON_SIZE, y, self.ICON_SIZE, self.ICON_SIZE
            )
            self._plus_icon.paint(painter, plus_rect)
            rects["add"] = plus_rect
            key = "library"

        self._hit_rects[key] = rects

    # --- Hit testing --------------------------------------------------

    def hit_test_tag(self, tag_name: str, pos) -> str | None:
        return self._match(f"tag:{tag_name}", pos)

    def hit_test_library(self, pos) -> str | None:
        return self._match("library", pos)

    def _match(self, key: str, pos) -> str | None:
        rects = self._hit_rects.get(key)
        if not rects:
            return None
        for action, rect in rects.items():
            if rect.contains(pos):
                return action
        return None


_ROLE_KIND = Qt.ItemDataRole.UserRole
_KIND_LIBRARY = "library"
_KIND_TEMPLATES = "templates"
_KIND_SETTINGS = "settings"
_KIND_TAG = "tag"


class SidebarTree(QTreeWidget):
    """QTreeWidget backed by ``tags.py``."""

    # kind ∈ {"library", "tag", "templates", "settings"}
    # path is the tag path (root → leaf) for kind=="tag", else [].
    selection_changed = Signal(str, list)

    # Emitted after a drag/drop reorders the tag tree.
    tag_tree_changed = Signal()

    # Emitted when a project card is dropped onto a tag node (or the
    # Library root). ``target_tag`` is the tag name, or None for the
    # Library root. The LibraryView decides what to do with it.
    project_drop_requested = Signal(int, object)

    # Action-icon requests — MainWindow opens the right dialog. The
    # add signal carries the parent tag name or None for root.
    tag_edit_requested = Signal(str)
    tag_delete_requested = Signal(str)
    tag_add_requested = Signal(object)  # parent tag name or None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebarNav")
        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setAnimated(False)
        # Double-click toggles expansion as a convenience alongside the
        # branch-chevron click — single click still navigates / selects.
        self.setExpandsOnDoubleClick(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setUniformRowHeights(True)
        # A little more indentation than strictly needed — leaves a few
        # pixels between the chevron and the selected-item left bar so
        # they don't visually collide at the gutter boundary.
        self.setIndentation(20)
        self.setIconSize(QSize(14, 14))

        # Override the palette's selection colors so the branch gutter
        # (where the chevron lives) + any paint paths that use the
        # palette instead of QSS match the rest of our selection
        # styling. Critically, we also set the *Inactive* group — Qt
        # falls back to it when the tree loses focus, and its default
        # Highlight color is near-white on a dark theme, which shows
        # as a white fill next to the chevron whenever focus moves to
        # the content area.
        palette = self.palette()
        selection_bg = QColor("#1F2222")      # A_DARK_1 — matches ::item:selected
        selection_fg = QColor("#FDCF00")      # MARIGOLD — selected text color
        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
        ):
            palette.setColor(group, QPalette.ColorRole.Highlight, selection_bg)
            palette.setColor(group, QPalette.ColorRole.HighlightedText, selection_fg)
        self.setPalette(palette)

        # Tag-item icon — rebuilt per-node in ``_populate_tags`` with
        # the tag's color (``tag_icon`` memoizes, so repeated lookups
        # for the same color are cheap).

        # Hover-reveal action icons on the right of each row. Tag rows
        # get add / edit / delete; the Library root gets just add.
        # Painted by the delegate; clicks are hit-tested here so Qt's
        # selection + drag gestures still fire when the press lands
        # outside the icon rects.
        self._actions_delegate = _TagActionsDelegate(
            QIcon(str(_ICONS_DIR / "plus.svg")),
            QIcon(str(_ICONS_DIR / "pencil.svg")),
            QIcon(str(_ICONS_DIR / "trash.svg")),
            self,
        )
        self.setItemDelegate(self._actions_delegate)
        # Mouse tracking so we get mouseMoveEvent without a button
        # held — needed to update the hover row as the cursor moves.
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

        # DragDrop mode so the tree accepts external drops (project
        # cards, content-area tag tiles) in addition to internal tag
        # moves. Default Qt move logic is bypassed in `dropEvent`.
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)

        self.currentItemChanged.connect(self._on_current_changed)

        self._library_item: QTreeWidgetItem | None = None
        self._tag_roots: list[tags.TagNode] = []
        self.rebuild()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def rebuild(self) -> None:
        """Reload the tag tree from disk and repopulate the widget."""
        self._tag_roots = tags.load()

        # Remember the selection by kind+path so rebuilds from drops don't
        # blank it out.
        previous_kind, previous_path = self._current_selection()

        self.blockSignals(True)
        self.clear()

        lib = QTreeWidgetItem(["Library"])
        lib.setData(0, _ROLE_KIND, _KIND_LIBRARY)
        # Library accepts tag drops (for promoting a tag to root), isn't
        # draggable itself.
        lib.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsDropEnabled
        )
        self.addTopLevelItem(lib)
        self._library_item = lib
        self._populate_tags(lib, self._tag_roots)
        lib.setExpanded(True)

        templates = QTreeWidgetItem(["Templates"])
        templates.setData(0, _ROLE_KIND, _KIND_TEMPLATES)
        templates.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        )
        self.addTopLevelItem(templates)

        settings_item = QTreeWidgetItem(["Settings"])
        settings_item.setData(0, _ROLE_KIND, _KIND_SETTINGS)
        settings_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        )
        self.addTopLevelItem(settings_item)

        self.blockSignals(False)

        # Restore selection. Falls back to Library root.
        if previous_kind == _KIND_TAG and previous_path:
            self.select_tag_path(previous_path)
        elif previous_kind == _KIND_TEMPLATES:
            self.setCurrentItem(templates)
        elif previous_kind == _KIND_SETTINGS:
            self.setCurrentItem(settings_item)
        else:
            self.setCurrentItem(lib)

    def _populate_tags(
        self, parent_item: QTreeWidgetItem, nodes: list[tags.TagNode]
    ) -> None:
        flags = (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsDragEnabled
            | Qt.ItemFlag.ItemIsDropEnabled
        )
        for node in nodes:
            item = QTreeWidgetItem([node.name])
            item.setData(0, _ROLE_KIND, _KIND_TAG)
            item.setFlags(flags)
            # The tag icon is filled with the tag's color (darkened)
            # and stroked with the color itself; tags without a color
            # fall back to the STEEL BLUE default inside `tag_icon`.
            item.setIcon(0, tag_icon(node.color, size=14))
            parent_item.addChild(item)
            self._populate_tags(item, node.children)
            item.setExpanded(True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select_library_root(self) -> None:
        if self._library_item is not None:
            self.setCurrentItem(self._library_item)

    def select_settings(self) -> None:
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            if item.data(0, _ROLE_KIND) == _KIND_SETTINGS:
                self.setCurrentItem(item)
                return

    def select_tag_path(self, path: list[str]) -> None:
        """Select the tree node matching `path` (empty = Library root)."""
        if not path:
            self.select_library_root()
            return
        cursor = self._library_item
        if cursor is None:
            return
        for name in path:
            match: QTreeWidgetItem | None = None
            for i in range(cursor.childCount()):
                child = cursor.child(i)
                if (
                    self._kind(child) == _KIND_TAG
                    and child.text(0) == name
                ):
                    match = child
                    break
            if match is None:
                return  # path not found — leave current selection alone
            cursor = match
        self.setCurrentItem(cursor)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def _kind(self, item: QTreeWidgetItem | None) -> str:
        if item is None:
            return ""
        return str(item.data(0, _ROLE_KIND) or "")

    def _tag_path(self, item: QTreeWidgetItem) -> list[str]:
        """Root-to-leaf tag names for a tag item."""
        names: list[str] = []
        cursor: QTreeWidgetItem | None = item
        while cursor is not None and self._kind(cursor) == _KIND_TAG:
            names.append(cursor.text(0))
            cursor = cursor.parent()
        names.reverse()
        return names

    def _current_selection(self) -> tuple[str, list[str]]:
        item = self.currentItem()
        kind = self._kind(item)
        if kind == _KIND_TAG:
            return _KIND_TAG, self._tag_path(item)
        return kind, []

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_current_changed(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        kind = self._kind(current)
        if kind == _KIND_TAG and current is not None:
            self.selection_changed.emit(_KIND_TAG, self._tag_path(current))
        elif kind in (_KIND_LIBRARY, _KIND_TEMPLATES, _KIND_SETTINGS):
            self.selection_changed.emit(kind, [])

    # ------------------------------------------------------------------
    # MIME — tag nodes export MIME_TAG so the content area can catch
    # them (and our own dropEvent routes uniformly via MIME payloads).
    # ------------------------------------------------------------------

    def mimeTypes(self) -> list[str]:  # type: ignore[override]
        # Advertise both payload types so Qt's default drag-move logic
        # (which paints the drop-indicator line between items) considers
        # our custom MIME data valid — otherwise ``canDropMimeData`` on
        # the underlying model rejects MIME_PROJECT and the indicator
        # never shows up.
        return [dnd.MIME_TAG, dnd.MIME_PROJECT]

    def mimeData(self, items) -> QMimeData:  # type: ignore[override]
        for item in items:
            if self._kind(item) == _KIND_TAG:
                return dnd.tag_mime(item.text(0))
        # Non-tag drags (e.g., attempting to drag Templates/Settings)
        # produce an empty payload so receivers ignore them.
        return QMimeData()

    # ------------------------------------------------------------------
    # DnD
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        mime = event.mimeData()
        if mime.hasFormat(dnd.MIME_TAG) or mime.hasFormat(dnd.MIME_PROJECT):
            # Delegate to QAbstractItemView so the drop-indicator state
            # gets initialized; force-accept afterwards to guarantee the
            # drag is allowed regardless of what the base class decides.
            super().dragEnterEvent(event)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        mime = event.mimeData()
        if mime.hasFormat(dnd.MIME_TAG) or mime.hasFormat(dnd.MIME_PROJECT):
            # super() updates the drop-indicator rect/position based on
            # cursor position, which is what paints the highlight line
            # / rectangle between or around items.
            super().dragMoveEvent(event)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        mime = event.mimeData()

        # Project drops — route to the enclosing LibraryView via signal.
        project_id = dnd.extract_project(mime)
        if project_id is not None:
            target = self.itemAt(event.position().toPoint())
            if target is None:
                event.ignore()
                return
            target_kind = self._kind(target)
            if target_kind == _KIND_LIBRARY:
                event.setDropAction(Qt.DropAction.IgnoreAction)
                event.accept()
                self.project_drop_requested.emit(project_id, None)
                return
            if target_kind == _KIND_TAG:
                event.setDropAction(Qt.DropAction.IgnoreAction)
                event.accept()
                self.project_drop_requested.emit(project_id, target.text(0))
                return
            event.ignore()
            return

        # Tag drops — internal move or external-from-TagTile.
        source_name = dnd.extract_tag(mime)
        if source_name is None:
            event.ignore()
            return

        target = self.itemAt(event.position().toPoint())
        if target is None:
            event.ignore()
            return

        drop_pos = self.dropIndicatorPosition()
        target_kind = self._kind(target)

        new_parent_name: str | None
        if drop_pos in (
            QAbstractItemView.DropIndicatorPosition.AboveItem,
            QAbstractItemView.DropIndicatorPosition.BelowItem,
        ):
            # Sibling drop — parent is the target's parent.
            target_parent = target.parent()
            parent_kind = self._kind(target_parent)
            if parent_kind == _KIND_LIBRARY:
                new_parent_name = None
            elif parent_kind == _KIND_TAG and target_parent is not None:
                new_parent_name = target_parent.text(0)
            else:
                event.ignore()
                return
        elif drop_pos == QAbstractItemView.DropIndicatorPosition.OnItem:
            if target_kind == _KIND_LIBRARY:
                new_parent_name = None
            elif target_kind == _KIND_TAG:
                new_parent_name = target.text(0)
            else:
                # Dropping onto Templates / Settings isn't allowed.
                event.ignore()
                return
        else:
            # OnViewport — dropping into empty space. Reject rather than
            # guess at user intent.
            event.ignore()
            return

        # No-op: drop lands in the tag's current container. Look it up
        # from `tags.py` rather than the widget so external drags work
        # even when the source item isn't selected.
        source_path = tags.path_of(self._tag_roots, source_name)
        if source_path is None:
            event.ignore()
            return
        current_parent_name: str | None = (
            source_path[-2] if len(source_path) >= 2 else None
        )
        if current_parent_name == new_parent_name:
            event.ignore()
            return

        try:
            tags.move_tag(self._tag_roots, source_name, new_parent_name)
        except tags.TagError:
            event.ignore()
            return

        tags.save(self._tag_roots)

        # IgnoreAction prevents Qt's default move from reinserting the
        # widget item after our rebuild — see the earlier commit that
        # fixed "tag disappears on nest".
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()

        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._finish_drop(source_name))

    def _finish_drop(self, moved_tag_name: str) -> None:
        """Rebuild + reselect after a successful tag move."""
        self.rebuild()
        path = tags.path_of(self._tag_roots, moved_tag_name) or []
        self.select_tag_path(path)
        self.tag_tree_changed.emit()

    # ------------------------------------------------------------------
    # Hover + click handling for the edit/trash action icons on the
    # right of tag rows. Hover updates which row paints the icons, and
    # press hit-tests against those icon rects before falling through
    # to Qt's default selection / drag behaviour.
    # ------------------------------------------------------------------

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        pos = event.position().toPoint()
        index = self.indexAt(pos)
        kind = index.data(_ROLE_KIND) if index.isValid() else None
        if kind in (_KIND_TAG, _KIND_LIBRARY):
            self._actions_delegate.set_hover_index(index)
        else:
            self._actions_delegate.set_hover_index(QModelIndex())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._actions_delegate.set_hover_index(QModelIndex())
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            item = self.itemAt(pos)
            if item is not None:
                kind = self._kind(item)
                if kind == _KIND_TAG:
                    hit = self._actions_delegate.hit_test_tag(item.text(0), pos)
                    if hit == "add":
                        self.tag_add_requested.emit(item.text(0))
                        event.accept()
                        return
                    if hit == "edit":
                        self.tag_edit_requested.emit(item.text(0))
                        event.accept()
                        return
                    if hit == "delete":
                        self.tag_delete_requested.emit(item.text(0))
                        event.accept()
                        return
                elif kind == _KIND_LIBRARY:
                    hit = self._actions_delegate.hit_test_library(pos)
                    if hit == "add":
                        # None → new tag lands at the root of the tree.
                        self.tag_add_requested.emit(None)
                        event.accept()
                        return
        super().mousePressEvent(event)
