"""Rename + color-pick dialog for a single tag.

Returns ``(new_name, new_color)`` on save or None on cancel. The
caller persists via ``tags.rename_tag`` / ``tags.set_tag_color`` and
rewrites any alpacka.yml files that reference a renamed tag.

Color picking uses ``QColorDialog`` so any web color is valid; the
dialog shows a live swatch of the current choice and a Clear button to
reset to "no color" (the default STEEL BLUE tint).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from .frameless import FramelessDialog


# Falls back to STEEL BLUE when the user hasn't picked a color — mirrors
# the default tag tint used elsewhere (chips + tile edge accent).
_DEFAULT_SWATCH = "#7D93AF"


class TagEditDialog(FramelessDialog):
    def __init__(
        self,
        original_name: str,
        current_color: str = "",
        *,
        title: str = "Edit tag",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, parent)
        self.resize(400, 0)
        self._result: tuple[str, str] | None = None
        self._color = current_color
        self._build_ui(original_name)

    @property
    def result(self) -> tuple[str, str] | None:
        return self._result

    @staticmethod
    def edit(
        parent: QWidget | None,
        name: str,
        color: str = "",
    ) -> tuple[str, str] | None:
        dlg = TagEditDialog(name, color, parent=parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.result
        return None

    @staticmethod
    def add(
        parent: QWidget | None,
        parent_hint: str | None = None,
    ) -> tuple[str, str] | None:
        """Open the dialog in "create new tag" mode.

        ``parent_hint`` is shown in the title so the user knows where
        the new tag will land (useful when the button lives on a
        specific sidebar row or content path).
        """
        title = (
            f"Add tag under '{parent_hint}'"
            if parent_hint
            else "Add tag"
        )
        dlg = TagEditDialog("", "", title=title, parent=parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.result
        return None

    # --- Layout --------------------------------------------------------

    def _build_ui(self, original_name: str) -> None:
        layout = self.content_layout

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)
        self._name_edit = QLineEdit(original_name)
        form.addRow("Name:", self._name_edit)

        # Color row: swatch preview + picker button + clear button.
        color_row = QWidget()
        color_layout = QHBoxLayout(color_row)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(8)

        self._color_preview = QLabel()
        self._color_preview.setObjectName("tagColorPreview")
        self._color_preview.setFixedSize(32, 32)
        color_layout.addWidget(self._color_preview)

        pick_btn = QPushButton("Choose color…")
        pick_btn.clicked.connect(self._on_pick_color)
        color_layout.addWidget(pick_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.setToolTip("Fall back to the default tag color.")
        clear_btn.clicked.connect(self._on_clear_color)
        color_layout.addWidget(clear_btn)

        color_layout.addStretch(1)
        form.addRow("Color:", color_row)

        layout.addLayout(form)

        self._update_preview()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        save_btn.setObjectName("primaryButton")
        save_btn.setDefault(True)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # --- Slots ---------------------------------------------------------

    def _on_pick_color(self) -> None:
        initial = QColor(self._color) if self._color else QColor(_DEFAULT_SWATCH)
        if not initial.isValid():
            initial = QColor(_DEFAULT_SWATCH)
        # Qt's built-in color dialog has a color wheel, hex input, and
        # web-safe picker — covers the "any web color" requirement.
        chosen = QColorDialog.getColor(
            initial, self, "Tag color", QColorDialog.ColorDialogOption.DontUseNativeDialog
        )
        if chosen.isValid():
            self._color = chosen.name()
            self._update_preview()

    def _on_clear_color(self) -> None:
        self._color = ""
        self._update_preview()

    def _update_preview(self) -> None:
        pm = QPixmap(32, 32)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        if self._color:
            painter.setBrush(QColor(self._color))
            painter.drawRoundedRect(0, 0, 32, 32, 5, 5)
        else:
            # "No color" — outlined dark tile with a diagonal slash so
            # the empty state is visually distinct from a dark-colored
            # swatch like black.
            painter.setBrush(QColor("#141312"))
            painter.drawRoundedRect(0, 0, 32, 32, 5, 5)
            pen = painter.pen()
            pen.setColor(QColor("#9D948B"))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawLine(6, 26, 26, 6)
        painter.end()
        self._color_preview.setPixmap(pm)
        self._color_preview.setToolTip(
            self._color if self._color else "No color (default tint)"
        )

    def _on_save(self) -> None:
        new_name = self._name_edit.text().strip()
        if not new_name:
            return
        self._result = (new_name, self._color)
        self.accept()
