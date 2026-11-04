"""Frameless dialog infrastructure.

All of the launcher's dialogs (and replacement for QMessageBox pop-ups)
are borderless with a compact title bar that matches the main window.

Three exports:

- ``FramelessDialog`` — QDialog base class. Subclasses add widgets to
  ``self.content_layout`` instead of setting a layout on ``self``.
- ``DialogTitleBar`` — the compact title bar. Usually not instantiated
  directly.
- ``MessageDialog`` — a minimal QMessageBox replacement with the same
  static helper style (``info``, ``warning``, ``critical``, ``question``,
  ``choose``).
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

_DIALOG_TITLE_BAR_HEIGHT = 32
_RESIZE_MARGIN = 8


class DialogTitleBar(QWidget):
    """Compact title bar used on modal dialogs (title + close button)."""

    def __init__(self, window: QWidget, title: str) -> None:
        super().__init__(window)
        self._window = window
        self.setObjectName("dialogTitleBar")
        self.setFixedHeight(_DIALOG_TITLE_BAR_HEIGHT)
        self._drag_anchor: QPoint | None = None
        self._build_ui(title)

    def _build_ui(self, title: str) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 0, 0)
        layout.setSpacing(0)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("dialogTitleBarTitle")
        self.title_label.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        )
        layout.addWidget(self.title_label, stretch=1)

        self.close_btn = QPushButton("\u2715")
        self.close_btn.setObjectName("titleBarCloseButton")
        self.close_btn.setFixedSize(42, _DIALOG_TITLE_BAR_HEIGHT)
        self.close_btn.setFlat(True)
        self.close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self._window.reject)
        layout.addWidget(self.close_btn)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    # Drag-to-move --------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._hit_caption(event):
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
            self._window.move(
                event.globalPosition().toPoint() - self._drag_anchor
            )
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_anchor = None
        super().mouseReleaseEvent(event)

    def _hit_caption(self, event: QMouseEvent) -> bool:
        return not self.close_btn.geometry().contains(event.position().toPoint())


class FramelessDialog(QDialog):
    """QDialog subclass with a custom title bar and native edge resize.

    Subclasses should add widgets to ``self.content_layout``. The `title`
    argument is also set as the windowTitle.
    """

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        resizable: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setModal(True)
        self._resizable = resizable

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._root_container = QWidget()
        self._root_container.setObjectName("dialogRoot")
        root = QVBoxLayout(self._root_container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        outer.addWidget(self._root_container)

        self._title_bar = DialogTitleBar(self, title)
        root.addWidget(self._title_bar)

        self._content = QWidget()
        self._content.setObjectName("dialogContent")
        self.content_layout = QVBoxLayout(self._content)
        self.content_layout.setContentsMargins(20, 18, 20, 18)
        self.content_layout.setSpacing(12)
        root.addWidget(self._content, stretch=1)

        if resizable and sys.platform != "win32":
            # macOS / Linux fallback — corner grip.
            grip_row = QHBoxLayout()
            grip_row.setContentsMargins(0, 0, 0, 0)
            grip_row.addStretch(1)
            grip_row.addWidget(QSizeGrip(self))
            root.addLayout(grip_row)

    # Update title bar text when windowTitle changes.
    def setWindowTitle(self, title: str) -> None:  # type: ignore[override]
        super().setWindowTitle(title)
        if hasattr(self, "_title_bar"):
            self._title_bar.set_title(title)

    # Native edge resize on Windows. Same approach as MainWindow — always
    # return an explicit (bool, int) tuple to keep PySide6 happy.
    def nativeEvent(self, event_type, message):  # type: ignore[override]
        if not self._resizable or sys.platform != "win32":
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
        # the window as a resize border. Convert the hit point to logical
        # pixels first so the edge math is unit-consistent.
        dpr = self.devicePixelRatioF() or 1.0
        frame = self.frameGeometry()
        local_x = x / dpr - frame.left()
        local_y = y / dpr - frame.top()
        w = frame.width()
        h = frame.height()

        m = _RESIZE_MARGIN
        on_left = local_x < m
        on_right = local_x > w - m
        on_top = local_y < m
        on_bottom = local_y > h - m

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
        return False, 0


# ---------------------------------------------------------------------------
# MessageDialog — drop-in replacement for QMessageBox convenience methods.
# ---------------------------------------------------------------------------


class MessageDialog(FramelessDialog):
    """Minimal message dialog used by `info`, `warning`, `critical`,
    `question`, and `choose` helpers below."""

    def __init__(
        self,
        title: str,
        text: str,
        buttons: list[str],
        *,
        primary_index: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, parent, resizable=False)
        self.resize(420, 0)
        self._clicked_index = -1

        label = QLabel(text)
        label.setObjectName("dialogMessage")
        label.setWordWrap(True)
        self.content_layout.addWidget(label)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        button_row.addStretch(1)
        for i, name in enumerate(buttons):
            btn = QPushButton(name)
            if i == primary_index:
                btn.setObjectName("primaryButton")
                btn.setDefault(True)
            btn.clicked.connect(lambda _=False, idx=i: self._pick(idx))
            button_row.addWidget(btn)
        self.content_layout.addLayout(button_row)

    def _pick(self, index: int) -> None:
        self._clicked_index = index
        # Treat the primary button as accept and any other as reject so
        # exec() callers that only care about accepted/rejected still work.
        if index == 0:
            self.accept()
        else:
            self.reject()

    @property
    def clicked_index(self) -> int:
        return self._clicked_index


def info(parent: QWidget | None, title: str, text: str) -> None:
    MessageDialog(title, text, ["OK"], parent=parent).exec()


def warning(parent: QWidget | None, title: str, text: str) -> None:
    MessageDialog(title, text, ["OK"], parent=parent).exec()


def critical(parent: QWidget | None, title: str, text: str) -> None:
    MessageDialog(title, text, ["OK"], parent=parent).exec()


def question(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    yes: str = "Yes",
    no: str = "No",
    default_yes: bool = False,
) -> bool:
    """Binary Yes/No prompt. Returns True if the first button was clicked."""
    primary = 0 if default_yes else 1
    dlg = MessageDialog(title, text, [yes, no], primary_index=primary, parent=parent)
    dlg.exec()
    return dlg.clicked_index == 0


def choose(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    buttons: list[str],
    primary_index: int = 0,
) -> int:
    """Multi-button prompt. Returns the 0-based index of the button clicked,
    or -1 if the dialog was closed via the title bar's close button."""
    dlg = MessageDialog(title, text, buttons, primary_index=primary_index, parent=parent)
    dlg.exec()
    return dlg.clicked_index
