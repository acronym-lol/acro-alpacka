"""Custom frameless-window title bar.

Renders the app title, minimize / maximize-restore / close buttons, and
handles drag-to-move + double-click-to-maximize. Edge resizing is handled
by the parent window via WM_NCHITTEST on Windows — see `main_window.py`.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QIcon, QMouseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

TITLE_BAR_HEIGHT = 36
_LOGO_SIZE = 20


class TitleBar(QWidget):
    def __init__(
        self,
        window: QWidget,
        title: str,
        *,
        icon_path: Path | None = None,
    ) -> None:
        super().__init__(window)
        self._window = window
        self.setObjectName("titleBar")
        self.setFixedHeight(TITLE_BAR_HEIGHT)
        self._drag_anchor: QPoint | None = None
        self._build_ui(title, icon_path)

    def _build_ui(self, title: str, icon_path: Path | None) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(0)

        if icon_path is not None and icon_path.is_file():
            self.logo_label = QLabel()
            self.logo_label.setObjectName("titleBarLogo")
            self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pixmap = QIcon(str(icon_path)).pixmap(QSize(_LOGO_SIZE, _LOGO_SIZE))
            self.logo_label.setPixmap(pixmap)
            self.logo_label.setFixedSize(_LOGO_SIZE, TITLE_BAR_HEIGHT)
            layout.addWidget(self.logo_label)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("titleBarTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.title_label, stretch=1)

        self.min_btn = self._make_button("\u2013")  # en-dash, aligns better than hyphen
        self.min_btn.setObjectName("titleBarButton")
        self.min_btn.clicked.connect(self._on_minimize)
        layout.addWidget(self.min_btn)

        self.max_btn = self._make_button("\u25A1")  # square
        self.max_btn.setObjectName("titleBarButton")
        self.max_btn.clicked.connect(self._on_toggle_maximize)
        layout.addWidget(self.max_btn)

        self.close_btn = self._make_button("\u2715")  # multiplication x
        self.close_btn.setObjectName("titleBarCloseButton")
        self.close_btn.clicked.connect(self._on_close)
        layout.addWidget(self.close_btn)

    def _make_button(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedSize(46, TITLE_BAR_HEIGHT)
        btn.setFlat(True)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    # --- Window controls --------------------------------------------------

    def _on_minimize(self) -> None:
        self._window.showMinimized()

    def _on_toggle_maximize(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self._update_max_icon()

    def _on_close(self) -> None:
        self._window.close()

    def _update_max_icon(self) -> None:
        # Switch between "maximize" and "restore" glyphs.
        self.max_btn.setText("\u2750" if self._window.isMaximized() else "\u25A1")

    def update_maximize_icon(self) -> None:
        """Public hook the main window calls on state changes."""
        self._update_max_icon()

    # --- Drag to move -----------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._hit_caption(event):
            if self._window.isMaximized():
                self._drag_anchor = None
            else:
                self._drag_anchor = (
                    event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
                )
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self._drag_anchor is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            new_pos = event.globalPosition().toPoint() - self._drag_anchor
            self._window.move(new_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_anchor = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._hit_caption(event):
            self._on_toggle_maximize()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def _hit_caption(self, event: QMouseEvent) -> bool:
        """Return True if the event is on the drag-sensitive region, not a button."""
        pos = event.position().toPoint()
        for btn in (self.min_btn, self.max_btn, self.close_btn):
            if btn.geometry().contains(pos):
                return False
        return True
