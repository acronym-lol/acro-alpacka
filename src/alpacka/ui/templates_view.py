"""Templates management view — list, add, edit, remove user templates."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import kinds, templates
from . import frameless
from .frameless import FramelessDialog


class _TemplateForm(FramelessDialog):
    """Shared form for adding and editing user templates."""

    def __init__(
        self,
        *,
        title: str,
        initial_name: str = "",
        initial_description: str = "",
        initial_toe_path: str = "",
        initial_kind: str = "touchdesigner",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, parent)
        self.resize(520, 0)

        layout = self.content_layout

        form = QFormLayout()
        form.setSpacing(8)

        self.kind_combo = QComboBox()
        for handler in kinds.all_handlers():
            self.kind_combo.addItem(handler.display_name, userData=handler.name)
        idx = self.kind_combo.findData(initial_kind)
        if idx >= 0:
            self.kind_combo.setCurrentIndex(idx)
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        form.addRow("Project type:", self.kind_combo)

        self.name_edit = QLineEdit(initial_name)
        self.name_edit.setPlaceholderText("My Template")
        form.addRow("Name:", self.name_edit)

        self.desc_edit = QTextEdit(initial_description)
        self.desc_edit.setFixedHeight(72)
        form.addRow("Description:", self.desc_edit)

        self._path_label = QLabel(".toe file:")
        path_row = QWidget()
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        self.path_edit = QLineEdit(initial_toe_path)
        path_layout.addWidget(self.path_edit, stretch=1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        path_layout.addWidget(browse)
        form.addRow(self._path_label, path_row)

        self._hint_label = QLabel()
        self._hint_label.setObjectName("pathPreview")
        self._hint_label.setWordWrap(True)
        form.addRow("", self._hint_label)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Drive the kind-dependent labels + placeholders off the initial kind.
        self._on_kind_changed()

    def _is_td_kind(self) -> bool:
        return (self.kind_combo.currentData() or "touchdesigner") == "touchdesigner"

    def _on_kind_changed(self) -> None:
        if self._is_td_kind():
            self._path_label.setText(".toe file:")
            self.path_edit.setPlaceholderText("Path to a .toe file...")
            self._hint_label.setText(
                "Pick a .toe that'll be copied into new projects (and its "
                "sibling `template.yml`, if any, will be applied)."
            )
        else:
            self._path_label.setText("Source folder:")
            self.path_edit.setPlaceholderText(
                "Path to a folder — optional for sourceless templates..."
            )
            self._hint_label.setText(
                "Pick a folder containing a `template.yml` (for declarative "
                "actions) or any loose files (copied verbatim into new "
                "projects). Leave blank for a bare kind declaration."
            )

    def _browse(self) -> None:
        start = self.path_edit.text().strip() or str(Path.home())
        if self._is_td_kind():
            chosen, _ = QFileDialog.getOpenFileName(
                self, "Choose .toe file", start, "TouchDesigner projects (*.toe)"
            )
        else:
            chosen = QFileDialog.getExistingDirectory(
                self, "Choose source folder", start
            )
        if chosen:
            self.path_edit.setText(chosen)

    def _on_ok(self) -> None:
        if not self.name_edit.text().strip():
            frameless.warning(self, "Missing field", "Name is required.")
            return

        path_text = self.path_edit.text().strip()
        if self._is_td_kind():
            if not path_text:
                frameless.warning(
                    self, "Missing field", "Template .toe path is required."
                )
                return
            if not Path(path_text).is_file():
                ok = frameless.question(
                    self,
                    "File not found",
                    "The .toe file does not exist on disk. Add anyway?",
                    yes="Add anyway",
                    no="Cancel",
                )
                if not ok:
                    return
        else:
            if path_text and not Path(path_text).exists():
                ok = frameless.question(
                    self,
                    "Path not found",
                    "The source path does not exist on disk. Add anyway?",
                    yes="Add anyway",
                    no="Cancel",
                )
                if not ok:
                    return
        self.accept()

    @property
    def name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def description(self) -> str:
        return self.desc_edit.toPlainText().strip()

    @property
    def toe_path(self) -> str:
        return self.path_edit.text().strip()

    @property
    def kind(self) -> str:
        return self.kind_combo.currentData() or "touchdesigner"


class TemplatesView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        toolbar = QHBoxLayout()
        heading = QLabel("Templates")
        heading.setObjectName("sectionHeading")
        toolbar.addWidget(heading)
        toolbar.addStretch(1)

        filter_label = QLabel("Filter by kind:")
        toolbar.addWidget(filter_label)
        self.kind_filter = QComboBox()
        self.kind_filter.addItem("All", userData=None)
        for handler in kinds.all_handlers():
            self.kind_filter.addItem(handler.display_name, userData=handler.name)
        self.kind_filter.currentIndexChanged.connect(self.refresh)
        toolbar.addWidget(self.kind_filter)

        add_btn = QPushButton("Add Template...")
        add_btn.setObjectName("primaryButton")
        add_btn.clicked.connect(self._on_add)
        toolbar.addWidget(add_btn)

        edit_btn = QPushButton("Edit")
        edit_btn.clicked.connect(self._on_edit)
        toolbar.addWidget(edit_btn)

        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._on_remove)
        toolbar.addWidget(remove_btn)

        layout.addLayout(toolbar)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.list_widget, stretch=1)

        self._hint = QLabel(
            "Built-in templates cannot be edited or removed. Add your own by pointing at any .toe on disk."
        )
        self._hint.setObjectName("templateDescription")
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

    def refresh(self) -> None:
        self.list_widget.clear()
        kind_filter = self.kind_filter.currentData() if hasattr(self, "kind_filter") else None
        for tmpl in templates.list_all(kind=kind_filter):
            label = tmpl.name
            tags: list[str] = []
            if tmpl.builtin:
                tags.append("built-in")
            if not tmpl.source_exists:
                tags.append("⚠ source missing")
            if tags:
                label = f"{label}   [{' · '.join(tags)}]"
            item = QListWidgetItem(
                f"{label}\n"
                f"  {tmpl.description}\n"
                f"  → {tmpl.toe_path or '(no path)'}"
            )
            item.setData(Qt.ItemDataRole.UserRole, tmpl.id)
            self.list_widget.addItem(item)

    def _selected_template(self) -> templates.Template | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        tid = item.data(Qt.ItemDataRole.UserRole)
        for t in templates.list_all():
            if t.id == tid:
                return t
        return None

    def _on_add(self) -> None:
        # Pre-select the kind the user is currently filtering by, so
        # switching filter → "Add Template" feels continuous.
        initial_kind = self.kind_filter.currentData() or "touchdesigner"
        dialog = _TemplateForm(
            title="Add Template",
            initial_kind=initial_kind,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            templates.add_user_template(
                dialog.name,
                dialog.description,
                dialog.toe_path,
                kind=dialog.kind,
            )
        except ValueError as exc:
            frameless.critical(self, "Could not add template", str(exc))
            return
        self.refresh()

    def _on_edit(self) -> None:
        tmpl = self._selected_template()
        if tmpl is None:
            frameless.info(self, "Edit Template", "Pick a template first.")
            return
        if tmpl.builtin:
            frameless.info(
                self, "Edit Template", "Built-in templates can't be edited."
            )
            return
        dialog = _TemplateForm(
            title="Edit Template",
            initial_name=tmpl.name,
            initial_description=tmpl.description,
            initial_toe_path=tmpl.toe_path,
            initial_kind=tmpl.kind,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            templates.update_user_template(
                tmpl.id,
                name=dialog.name,
                description=dialog.description,
                toe_path=dialog.toe_path,
                kind=dialog.kind,
            )
        except ValueError as exc:
            frameless.critical(self, "Could not update template", str(exc))
            return
        self.refresh()

    def _on_remove(self) -> None:
        tmpl = self._selected_template()
        if tmpl is None:
            frameless.info(self, "Remove Template", "Pick a template first.")
            return
        if tmpl.builtin:
            frameless.info(
                self, "Remove Template", "Built-in templates can't be removed."
            )
            return
        if not frameless.question(
            self,
            "Remove template?",
            f"Remove '{tmpl.name}' from the library?\n\nThe .toe file on disk will not be touched.",
            yes="Remove",
            no="Cancel",
        ):
            return
        try:
            templates.delete_user_template(tmpl.id)
        except ValueError as exc:
            frameless.critical(self, "Could not remove template", str(exc))
            return
        self.refresh()
