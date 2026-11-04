"""Multi-select tag picker modal + reusable chip-bar widget.

Shared by New Project, Edit Details, and Add Existing — all three now
hand tag-editing off to ``TagPickerDialog.pick`` instead of using a
free-form comma-separated ``QLineEdit``.

The dialog renders the full tag tree with one checkbox per node (indented
by depth), plus an inline "+ Add" row that creates new tags directly in
``tags.json`` so the sidebar tree stays in sync. Tree ordering is
preserved in the returned list.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import tags
from . import frameless
from .frameless import FramelessDialog


class TagChipBar(QWidget):
    """Row of tag chips + an "Edit tags…" button.

    Emits ``edit_requested`` when the user clicks the edit button. The
    owning dialog is responsible for opening the picker and calling
    ``set_tags`` with the result.
    """

    edit_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tags: list[str] = []
        self._build_ui()

    def _build_ui(self) -> None:
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self._chip_host = QWidget()
        self._chip_layout = QHBoxLayout(self._chip_host)
        self._chip_layout.setContentsMargins(0, 0, 0, 0)
        self._chip_layout.setSpacing(4)
        row.addWidget(self._chip_host, stretch=1)

        self._edit_btn = QPushButton("Edit tags…")
        self._edit_btn.clicked.connect(
            lambda _checked=False: self.edit_requested.emit()
        )
        row.addWidget(self._edit_btn)

        self._render_chips()

    def set_tags(self, tag_names: list[str]) -> None:
        self._tags = list(tag_names)
        self._render_chips()

    def tags(self) -> list[str]:
        return list(self._tags)

    def _render_chips(self) -> None:
        while self._chip_layout.count() > 0:
            item = self._chip_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not self._tags:
            empty = QLabel("(no tags)")
            empty.setObjectName("pathPreview")
            self._chip_layout.addWidget(empty)
        else:
            for name in self._tags:
                chip = QLabel(name)
                chip.setObjectName("tagChip")
                self._chip_layout.addWidget(chip)
        self._chip_layout.addStretch(1)


class TagPickerDialog(FramelessDialog):
    """Modal for picking tags. Produces the tree order of checked names."""

    def __init__(
        self,
        *,
        selected: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Select Tags", parent)
        self.resize(380, 500)
        self._selected: set[str] = set(selected or [])
        self._checkboxes: dict[str, QCheckBox] = {}
        self._result: list[str] | None = None

        # Load the tree and auto-ensure any already-assigned tag is
        # represented so the picker can't "lose" a tag the project has.
        self._tree_roots: list[tags.TagNode] = tags.load()
        if tags.ensure_tags_exist(self._tree_roots, self._selected):
            tags.save(self._tree_roots)

        self._build_ui()

    # --- Public API ----------------------------------------------------

    @property
    def result_tags(self) -> list[str] | None:
        return self._result

    @staticmethod
    def pick(
        parent: QWidget | None, selected: list[str]
    ) -> list[str] | None:
        """Open the picker modally. Returns the picked names or None on cancel."""
        dlg = TagPickerDialog(selected=selected, parent=parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.result_tags
        return None

    # --- Layout --------------------------------------------------------

    def _build_ui(self) -> None:
        layout = self.content_layout

        hint = QLabel(
            "Pick one or more tags for this project. "
            "Create a new tag at the bottom if you need one."
        )
        hint.setObjectName("pathPreview")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("tagPickerScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._list_host = QWidget()
        self._list_host.setObjectName("tagPickerList")
        # Without WA_StyledBackground the QSS background on a plain
        # QWidget doesn't paint — it falls through to whatever bg Qt
        # picks for unstyled widgets (often near-white).
        self._list_host.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True
        )
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(6, 6, 6, 6)
        self._list_layout.setSpacing(2)
        self._scroll.setWidget(self._list_host)
        layout.addWidget(self._scroll, stretch=1)
        self._rebuild_list()

        new_row = QHBoxLayout()
        new_row.setSpacing(6)
        self._new_tag_edit = QLineEdit()
        self._new_tag_edit.setPlaceholderText("New tag name…")
        self._new_tag_edit.textChanged.connect(self._update_new_btn)
        self._new_tag_edit.returnPressed.connect(self._on_add_new)
        new_row.addWidget(self._new_tag_edit, stretch=1)
        self._new_btn = QPushButton("+ Add")
        self._new_btn.setEnabled(False)
        self._new_btn.clicked.connect(
            lambda _checked=False: self._on_add_new()
        )
        new_row.addWidget(self._new_btn)
        layout.addLayout(new_row)

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

    def _rebuild_list(self) -> None:
        # Clear previous rows.
        while self._list_layout.count() > 0:
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._checkboxes.clear()

        if not self._tree_roots:
            empty = QLabel("No tags yet — add one below.")
            empty.setObjectName("pathPreview")
            self._list_layout.addWidget(empty)
            self._list_layout.addStretch(1)
            return

        def walk(nodes: list[tags.TagNode], depth: int) -> None:
            for node in nodes:
                cb = QCheckBox(node.name)
                cb.setChecked(node.name in self._selected)
                cb.stateChanged.connect(
                    lambda _state, name=node.name: self._on_toggle(name)
                )
                wrapper = QWidget()
                row = QHBoxLayout(wrapper)
                row.setContentsMargins(16 * depth, 0, 0, 0)
                row.setSpacing(0)
                row.addWidget(cb)
                row.addStretch(1)
                self._list_layout.addWidget(wrapper)
                self._checkboxes[node.name] = cb
                walk(node.children, depth + 1)

        walk(self._tree_roots, 0)
        self._list_layout.addStretch(1)

    # --- Slots ---------------------------------------------------------

    def _on_toggle(self, name: str) -> None:
        cb = self._checkboxes.get(name)
        if cb is None:
            return
        if cb.isChecked():
            self._selected.add(name)
        else:
            self._selected.discard(name)

    def _update_new_btn(self) -> None:
        self._new_btn.setEnabled(bool(self._new_tag_edit.text().strip()))

    def _on_add_new(self) -> None:
        name = self._new_tag_edit.text().strip()
        if not name:
            return
        try:
            tags.add_tag(self._tree_roots, name)
        except tags.TagError as exc:
            frameless.warning(self, "Could not add tag", str(exc))
            return
        tags.save(self._tree_roots)
        self._selected.add(name)
        self._new_tag_edit.clear()
        self._rebuild_list()

    def _on_save(self) -> None:
        # Return tags in tree order (depth-first) so the saved list is
        # stable across saves even when the user toggles in random order.
        ordered: list[str] = []
        for node in tags.walk(self._tree_roots):
            if node.name in self._selected:
                ordered.append(node.name)
        # Include any selected names that, somehow, aren't in the tree
        # (defensive — ensure_tags_exist at open should make this empty).
        for name in self._selected:
            if name not in ordered:
                ordered.append(name)
        self._result = ordered
        self.accept()
