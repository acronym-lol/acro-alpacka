"""Add Existing Project dialog."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from .. import add_existing, kinds, project
from . import frameless
from .frameless import FramelessDialog
from .tag_picker_dialog import TagChipBar, TagPickerDialog


class AddExistingDialog(FramelessDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Add Existing Project", parent)
        self.resize(520, 0)
        self._row_id: int | None = None
        self._candidates: list[str] = []
        self._detection_suppressed = False
        self._build_ui()

    @property
    def result_row_id(self) -> int | None:
        return self._row_id

    def _build_ui(self) -> None:
        layout = self.content_layout

        heading = QLabel(
            "Pick the project's folder. ALPACKA will auto-detect the kind "
            "(TouchDesigner, Node.js, Python, etc.) — override below if needed."
        )
        heading.setWordWrap(True)
        layout.addWidget(heading)

        folder_row = QWidget()
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Project folder...")
        self.folder_edit.textChanged.connect(self._on_folder_changed)
        folder_layout.addWidget(self.folder_edit, stretch=1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse_folder)
        folder_layout.addWidget(browse)
        layout.addWidget(folder_row)

        # Detected kind + user override.
        kind_form = QFormLayout()
        kind_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.kind_combo = QComboBox()
        for handler in kinds.all_handlers():
            self.kind_combo.addItem(handler.display_name, userData=handler.name)
        self.kind_combo.currentIndexChanged.connect(self._on_kind_override)
        kind_form.addRow("Project type:", self.kind_combo)
        layout.addLayout(kind_form)

        self.signals_label = QLabel()
        self.signals_label.setObjectName("pathPreview")
        self.signals_label.setWordWrap(True)
        layout.addWidget(self.signals_label)

        # TD-only .toe picker (shown when kind = touchdesigner and multiple
        # candidates were found).
        self.toe_combo = QComboBox()
        self.toe_combo.setVisible(False)
        layout.addWidget(self.toe_combo)

        self.status_label = QLabel()
        self.status_label.setObjectName("pathPreview")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        tag_form = QFormLayout()
        tag_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.tag_bar = TagChipBar()
        self.tag_bar.edit_requested.connect(self._on_edit_tags)
        tag_form.addRow("Tags:", self.tag_bar)
        layout.addLayout(tag_form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.add_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.add_btn.setText("Add")
        self.add_btn.setEnabled(False)
        buttons.accepted.connect(self._on_add)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_folder(self) -> None:
        start = self.folder_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose project folder", start
        )
        if chosen:
            self.folder_edit.setText(chosen)

    def _on_folder_changed(self) -> None:
        self.toe_combo.clear()
        self.toe_combo.setVisible(False)
        self.add_btn.setEnabled(False)
        self.status_label.setText("")
        self.signals_label.setText("")
        self._candidates = []

        folder_str = self.folder_edit.text().strip()
        if not folder_str:
            self.tag_bar.set_tags([])
            return
        folder = Path(folder_str)
        if not folder.is_dir():
            self.tag_bar.set_tags([])
            return

        # Auto-detect kind across all registered handlers.
        ranked = kinds.detect_kind(folder)
        if ranked:
            top_name, top_result = ranked[0]
            signals_text = ", ".join(top_result.signals) if top_result.signals else ""
            summary = f"Detected: {kinds.get(top_name).display_name}"
            if signals_text:
                summary += f" ({signals_text})"
            # Flag near-ties so the user knows to verify.
            if len(ranked) > 1 and (top_result.confidence - ranked[1][1].confidence) < 0.15:
                runner = kinds.get(ranked[1][0]).display_name
                summary += f"\nAlso possible: {runner} — override below if needed."
            self.signals_label.setText(summary)

            self._detection_suppressed = True
            idx = self.kind_combo.findData(top_name)
            if idx >= 0:
                self.kind_combo.setCurrentIndex(idx)
            self._detection_suppressed = False
        else:
            self.signals_label.setText("Nothing to auto-detect — pick a type below.")

        self._refresh_for_kind(folder)

        # Preload tags from any existing alpacka.yml.
        existing = project.load(folder)
        self.tag_bar.set_tags(list(existing.tags) if existing else [])

    def _on_kind_override(self) -> None:
        if self._detection_suppressed:
            return
        folder_str = self.folder_edit.text().strip()
        if folder_str and Path(folder_str).is_dir():
            self._refresh_for_kind(Path(folder_str))

    def _refresh_for_kind(self, folder: Path) -> None:
        """Re-evaluate the TD-specific candidate list for the chosen kind."""
        kind_name = self.kind_combo.currentData() or kinds.default_kind()
        self.toe_combo.clear()
        self.toe_combo.setVisible(False)

        if kind_name == kinds.default_kind():
            try:
                scan = add_existing.scan_folder(folder)
            except add_existing.AddExistingError as exc:
                self.status_label.setText(f"⚠ {exc}")
                self.add_btn.setEnabled(False)
                return
            if not scan.candidate_toes:
                self.status_label.setText(
                    "⚠ No .toe file found (auto-numbered backups are ignored)."
                )
                self.add_btn.setEnabled(False)
                return
            self._candidates = [str(t) for t in scan.candidate_toes]
            if len(self._candidates) == 1:
                self.status_label.setText(
                    f"Will import: {folder / self._candidates[0]}"
                )
            else:
                self.toe_combo.setVisible(True)
                for toe in self._candidates:
                    self.toe_combo.addItem(toe)
                self.toe_combo.currentIndexChanged.connect(self._update_alternates_hint)
                self._update_alternates_hint()
        else:
            self._candidates = []
            self.status_label.setText(
                f"Will import: {folder} as a "
                f"{kinds.get(kind_name).display_name} project"
            )

        self.add_btn.setEnabled(True)

    def _update_alternates_hint(self) -> None:
        if len(self._candidates) <= 1:
            return
        primary = self.toe_combo.currentText()
        alternates = [c for c in self._candidates if c != primary]
        folder = Path(self.folder_edit.text().strip())
        hint = f"Primary: {folder / primary}"
        if alternates:
            hint += (
                f"\nAlternates (launchable from the ⋯ menu): "
                + ", ".join(alternates)
            )
        self.status_label.setText(hint)

    def _on_edit_tags(self) -> None:
        result = TagPickerDialog.pick(self, self.tag_bar.tags())
        if result is not None:
            self.tag_bar.set_tags(result)

    def _on_add(self) -> None:
        folder = Path(self.folder_edit.text().strip())
        kind_name = self.kind_combo.currentData() or kinds.default_kind()
        toe_file = (
            self.toe_combo.currentText()
            if (
                kind_name == kinds.default_kind()
                and self.toe_combo.isVisible()
                and self._candidates
            )
            else None
        )

        try:
            row_id = add_existing.add_existing(
                folder, toe_file=toe_file, tags=self.tag_bar.tags(), kind=kind_name
            )
        except add_existing.AddExistingError as exc:
            frameless.critical(self, "Could not add project", str(exc))
            return

        self._row_id = row_id
        self.accept()
