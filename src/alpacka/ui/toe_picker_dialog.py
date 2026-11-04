"""Modal for choosing which .toe files a project's Launch button opens.

Replaces the inline ``QMenu`` that used to live on the card. A menu
forced the user to reopen it after every toggle; a modal with Save /
Cancel lets them make multiple picks and confirm once.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .frameless import FramelessDialog


class ToePickerDialog(FramelessDialog):
    """Checkbox list of a project's .toe files + Save / Cancel."""

    def __init__(
        self,
        project_name: str,
        all_toes: list[str],
        selected: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Select .toe files to launch", parent)
        self.resize(380, 0)
        self._all_toes = list(all_toes)
        self._selected = set(selected)
        self._result: list[str] | None = None
        self._boxes: list[QCheckBox] = []
        self._build_ui(project_name)

    # --- Public API ----------------------------------------------------

    @property
    def result_toes(self) -> list[str] | None:
        return self._result

    @staticmethod
    def pick(
        parent: QWidget | None,
        project_name: str,
        all_toes: list[str],
        selected: list[str],
    ) -> list[str] | None:
        """Open the modal. Returns the picked list on Save, None on Cancel."""
        dlg = ToePickerDialog(project_name, all_toes, selected, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.result_toes
        return None

    # --- Layout --------------------------------------------------------

    def _build_ui(self, project_name: str) -> None:
        layout = self.content_layout

        hint = QLabel(
            f"Pick which .toe files open when you launch {project_name!r}. "
            "Unchecked files stay launchable individually from the ⋯ menu."
        )
        hint.setObjectName("pathPreview")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        for toe in self._all_toes:
            cb = QCheckBox(toe)
            cb.setChecked(toe in self._selected)
            self._boxes.append(cb)
            layout.addWidget(cb)

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

    def _on_save(self) -> None:
        picked = [cb.text() for cb in self._boxes if cb.isChecked()]
        # Guarantee at least the primary launches — matches the
        # "empty default_toes = launch primary" fallback elsewhere. If
        # the user unchecks everything, treat it as "fall back to
        # primary" by emitting that.
        if not picked and self._all_toes:
            picked = [self._all_toes[0]]
        self._result = picked
        self.accept()
