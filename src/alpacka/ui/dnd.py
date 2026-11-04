"""Shared drag-and-drop MIME helpers.

Two custom payload types travel between widgets:

- ``application/x-alpacka-tag`` — a tag name, dragged from a sidebar tree
  node or a content-area tag tile.
- ``application/x-alpacka-project`` — a library row id (integer), dragged
  from a project card.

Keeping this logic in one place means sources, targets, and the MIME
contract stay in sync.
"""
from __future__ import annotations

from PySide6.QtCore import QMimeData, QPoint, Qt
from PySide6.QtGui import QColor, QDrag, QPainter, QPixmap, QPolygon
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QWidget

MIME_TAG = "application/x-alpacka-tag"
MIME_PROJECT = "application/x-alpacka-project"


def tag_mime(name: str) -> QMimeData:
    md = QMimeData()
    md.setData(MIME_TAG, name.encode("utf-8"))
    return md


def project_mime(row_id: int) -> QMimeData:
    md = QMimeData()
    md.setData(MIME_PROJECT, str(int(row_id)).encode("utf-8"))
    return md


def extract_tag(mime: QMimeData) -> str | None:
    if not mime.hasFormat(MIME_TAG):
        return None
    raw = bytes(mime.data(MIME_TAG)).decode("utf-8", errors="replace")
    return raw or None


def extract_project(mime: QMimeData) -> int | None:
    if not mime.hasFormat(MIME_PROJECT):
        return None
    raw = bytes(mime.data(MIME_PROJECT)).decode("utf-8", errors="replace")
    try:
        return int(raw)
    except ValueError:
        return None


def fade_pixmap(pixmap: QPixmap, opacity: float = 0.5) -> QPixmap:
    """Return a copy of `pixmap` rendered onto a transparent canvas at
    the given opacity — used as the drag preview so the thing traveling
    with the cursor reads as semi-transparent."""
    faded = QPixmap(pixmap.size())
    faded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(faded)
    painter.setOpacity(opacity)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return faded


def suppress_forbidden_cursor(drag: QDrag) -> None:
    """Replace the default "not allowed" cursor with a plain arrow.

    Qt paints a red strikethrough circle whenever the drag hovers over
    an area that isn't a valid drop target — which is most of the UI
    between source and destination, making the drag feel blocked even
    while just moving across it. ``setDragCursor`` takes a pixmap and
    draws it *as* the cursor for that action, so a small arrow pixmap
    gives us a neutral "still dragging" indicator in place of the
    forbidden icon. Move / Copy actions aren't overridden, so valid
    drop targets keep the standard Qt drag cursor.
    """
    drag.setDragCursor(_arrow_cursor_pixmap(), Qt.DropAction.IgnoreAction)


class DragSource:
    """Mixin providing drag-initiation plumbing for widgets.

    Subclass **before** the Qt base class and:
      1. Call ``self._init_drag_source()`` in ``__init__``.
      2. Implement ``_drag_mime()`` returning the payload.

    The mixin handles:
      - Press/move threshold detection (``QApplication.startDragDistance``).
      - Building the ``QDrag`` with a 50%-faded widget pixmap preview.
      - Suppressing the red forbidden cursor over non-drop zones.
      - Dimming the source widget to 50% during the drag via a swap of
        graphics effects; restoring the original via ``_restore_effect``.

    The restore hook lets subclasses re-apply their own drop-shadow —
    ``setGraphicsEffect`` takes ownership and destroys the previous
    effect, so restoration must rebuild, not just swap back.
    """

    # Preview pixmap width (auto-scaled, aspect-preserving).
    DRAG_PREVIEW_WIDTH = 240
    DRAG_PREVIEW_OPACITY = 0.5
    SOURCE_DIM_OPACITY = 0.5

    # --- Hooks subclasses override ------------------------------------

    def _drag_mime(self) -> QMimeData:
        raise NotImplementedError

    def _restore_effect(self) -> None:
        """Restore the widget's normal graphics effect after a drag.

        Default: remove any lingering effect. Subclasses with a
        drop-shadow should override to rebuild it.
        """
        assert isinstance(self, QWidget)
        self.setGraphicsEffect(None)

    def _drag_preview_hotspot(self, pixmap: QPixmap) -> QPoint:
        """Anchor point for the drag pixmap relative to the cursor."""
        return QPoint(pixmap.width() // 2, pixmap.height() // 2)

    # --- Wiring -------------------------------------------------------

    def _init_drag_source(self) -> None:
        self._drag_press_pos: QPoint | None = None

    # Qt event handlers. Subclasses inherit these naturally via Python's
    # MRO as long as DragSource comes before the Qt base class.
    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_press_pos = event.position().toPoint()
        super().mousePressEvent(event)  # type: ignore[misc]

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if (
            self._drag_press_pos is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            distance = (
                event.position().toPoint() - self._drag_press_pos
            ).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._start_drag()
                return
        super().mouseMoveEvent(event)  # type: ignore[misc]

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._drag_press_pos = None
        super().mouseReleaseEvent(event)  # type: ignore[misc]

    # --- Drag operation ----------------------------------------------

    def _start_drag(self) -> None:
        assert isinstance(self, QWidget)
        self._drag_press_pos = None
        # Clear any pressed state a button-subclass may be showing so
        # the grabbed pixmap captures the resting appearance.
        if hasattr(self, "setDown"):
            self.setDown(False)

        drag = QDrag(self)
        drag.setMimeData(self._drag_mime())

        pixmap = self.grab().scaledToWidth(
            self.DRAG_PREVIEW_WIDTH,
            Qt.TransformationMode.SmoothTransformation,
        )
        preview = fade_pixmap(pixmap, self.DRAG_PREVIEW_OPACITY)
        drag.setPixmap(preview)
        drag.setHotSpot(self._drag_preview_hotspot(preview))
        suppress_forbidden_cursor(drag)

        # Dim the source widget in place for the duration of the drag.
        opacity = QGraphicsOpacityEffect(self)
        opacity.setOpacity(self.SOURCE_DIM_OPACITY)
        self.setGraphicsEffect(opacity)
        try:
            drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction)
        finally:
            # The drop can trigger a library refresh that destroys this
            # card before ``drag.exec`` returns. Touching ``self`` via
            # Qt APIs in that case raises "Internal C++ object already
            # deleted" — harmless to the user, but noisy in the log. A
            # broad catch is fine: the widget going away is exactly
            # when "restore" is moot.
            try:
                self._restore_effect()
            except RuntimeError:
                pass


def _arrow_cursor_pixmap() -> QPixmap:
    """Tiny arrow pixmap used as the IgnoreAction drag cursor.

    Painted rather than shipped as a file because we only need a
    cursor-sized glyph that reads as "pointer" — matching the OS
    pixel-perfect isn't worth the platform-specific plumbing.
    """
    pm = QPixmap(20, 20)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # Standard pointer arrow — tip at (1,1), tail at bottom-right.
    arrow = QPolygon(
        [
            QPoint(1, 1),
            QPoint(1, 15),
            QPoint(5, 11),
            QPoint(8, 17),
            QPoint(10, 16),
            QPoint(7, 10),
            QPoint(12, 10),
        ]
    )
    painter.setBrush(QColor("#ECECEC"))
    painter.setPen(QColor("#0C0C0C"))
    painter.drawPolygon(arrow)
    painter.end()
    return pm
