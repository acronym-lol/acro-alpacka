"""TouchDesigner preferences UI — the shared table + first-run prompt.

`TDPrefsTable` lists the curated preferences from `td_prefs.RECOMMENDED`
with each one's current value in `pref.txt` next to the value ALPACKA
would set. The first-run prompt (`TDPrefsPromptDialog`) and the Settings
section both use it, so the user always sees exactly what will change
before anything is written.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import external, td_prefs
from .frameless import FramelessDialog


class _Row:
    def __init__(self, spec: td_prefs.PrefSpec) -> None:
        self.spec = spec
        self.checkbox = QCheckBox()
        self.current = QLabel()
        self.current.setWordWrap(True)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.value_label: QLabel | None = None
        self.path_edit: QLineEdit | None = None

    def proposed(self) -> str:
        if self.path_edit is not None:
            return self.path_edit.text().strip()
        return self.spec.value or ""


class TDPrefsTable(QWidget):
    """Checkbox | preference | now | → | ALPACKA sets | note."""

    changed = Signal()

    def __init__(
        self,
        specs: list[td_prefs.PrefSpec],
        *,
        mode: str = "settings",  # "settings" | "prompt"
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mode = mode
        self._rows: list[_Row] = []
        self._managed: dict[str, str] = {}
        self._vscode = external.resolve_vscode_editor() or ""

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(2)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(2, 2)
        grid.setColumnStretch(4, 3)
        grid.setColumnStretch(5, 2)

        for col, text in ((1, "Preference"), (2, "Now"), (4, "ALPACKA sets")):
            header = QLabel(text)
            header.setObjectName("tertiaryHeading")
            grid.addWidget(header, 0, col)

        # Two grid rows per preference: the values, then the description
        # spanning the full width.
        for i, spec in enumerate(specs):
            r = 1 + i * 2
            row = _Row(spec)
            row.checkbox.setToolTip("Let ALPACKA manage this preference")
            row.checkbox.toggled.connect(lambda _checked=False: self.changed.emit())
            grid.addWidget(row.checkbox, r, 0)

            title = QLabel(spec.label)
            title.setToolTip(spec.key)
            grid.addWidget(title, r, 1)
            grid.addWidget(row.current, r, 2)
            grid.addWidget(QLabel("→"), r, 3)
            if spec.kind == "path":
                grid.addWidget(self._build_path_cell(row), r, 4)
            else:
                row.value_label = QLabel(td_prefs.display_value(spec.key, spec.value))
                grid.addWidget(row.value_label, r, 4)
            grid.addWidget(row.note, r, 5)

            desc = QLabel(f"{spec.description}  ({spec.key})")
            desc.setObjectName("templateDescription")
            desc.setWordWrap(True)
            desc.setContentsMargins(0, 0, 0, 10)
            grid.addWidget(desc, r + 1, 1, 1, 5)
            self._rows.append(row)

    def _build_path_cell(self, row: _Row) -> QWidget:
        cell = QWidget()
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        row.path_edit = QLineEdit()
        row.path_edit.setMinimumWidth(220)
        row.path_edit.setPlaceholderText("Path to the editor")
        row.path_edit.textChanged.connect(lambda _t="", r=row: self._on_path_edited(r))
        layout.addWidget(row.path_edit, stretch=1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda _checked=False, r=row: self._browse(r))
        layout.addWidget(browse)
        return cell

    def _on_path_edited(self, row: _Row) -> None:
        has_value = bool(row.proposed())
        row.checkbox.setEnabled(has_value)
        if not has_value and row.checkbox.isChecked():
            row.checkbox.setChecked(False)
        self.changed.emit()

    def _browse(self, row: _Row) -> None:
        start = row.proposed() or str(Path.home())
        chosen, _ = QFileDialog.getOpenFileName(self, f"Choose {row.spec.label.lower()}", start)
        if chosen and row.path_edit is not None:
            row.path_edit.setText(chosen)

    # --- State -----------------------------------------------------------

    def set_values(
        self,
        state: td_prefs.State,
        current: dict[str, str],
        fstate: td_prefs.FileState | None = None,
        *,
        keep_edits: bool = False,
    ) -> None:
        """Fill the table from saved decisions and the file's current values.

        `keep_edits=True` only refreshes the "Now" column and notes, so a
        background launch doesn't wipe edits the user hasn't applied yet.
        """
        fstate = fstate or td_prefs.FileState()
        keep = keep_edits and self.is_dirty()
        self._managed = dict(state.managed)
        declined = set(state.declined)

        for row in self._rows:
            key = row.spec.key
            value = current.get(key)
            if row.spec.kind == "path" and value and value.strip():
                # Full paths don't fit the column; show the file, tooltip the path.
                row.current.setText(Path(value.strip()).name)
                row.current.setToolTip(value)
            else:
                row.current.setText(td_prefs.display_value(key, value))
                row.current.setToolTip("")
            if not keep:
                if row.path_edit is not None:
                    value = state.managed.get(key) or self._vscode or current.get(key, "")
                    row.path_edit.blockSignals(True)
                    row.path_edit.setText(value)
                    row.path_edit.blockSignals(False)
                    row.checkbox.setEnabled(bool(value))
                if self._mode == "prompt":
                    checked = key in state.managed or (
                        row.spec.default_checked and key not in declined
                    )
                else:
                    checked = key in state.managed
                row.checkbox.blockSignals(True)
                row.checkbox.setChecked(checked and row.checkbox.isEnabled())
                row.checkbox.blockSignals(False)
            self._set_note(row, current, fstate)

    def _set_note(
        self, row: _Row, current: dict[str, str], fstate: td_prefs.FileState
    ) -> None:
        key, kind = row.spec.key, row.spec.kind
        effective = td_prefs.effective_value(key, current)
        note, warn = "", False
        if self._mode == "prompt":
            proposed = row.proposed()
            if proposed and td_prefs.values_equal(effective, proposed, kind):
                note = "Already set"
        elif key in self._managed:
            wanted = self._managed[key]
            if not td_prefs.values_equal(effective, wanted, kind):
                written = fstate.written.get(key)
                if written is not None and not td_prefs.values_equal(effective, written, kind):
                    note, warn = "Changed outside ALPACKA, set back at next launch", True
                else:
                    note = "Applied at next launch"
        elif key in fstate.originals:
            note = "Restored at next launch"
        row.note.setText(note)
        row.note.setObjectName("tdPrefsWarning" if warn else "templateDescription")
        row.note.style().unpolish(row.note)
        row.note.style().polish(row.note)

    def pending_changes(self) -> tuple[dict[str, str], list[str]]:
        """(accepted {key: value}, declined [keys]) from the checkboxes."""
        accept: dict[str, str] = {}
        decline: list[str] = []
        for row in self._rows:
            if row.checkbox.isChecked() and row.proposed():
                accept[row.spec.key] = row.proposed()
            else:
                decline.append(row.spec.key)
        return accept, decline

    def is_dirty(self) -> bool:
        for row in self._rows:
            key = row.spec.key
            if row.checkbox.isChecked() != (key in self._managed):
                return True
            if row.checkbox.isChecked() and row.path_edit is not None:
                if not td_prefs.values_equal(row.proposed(), self._managed.get(key), "path"):
                    return True
        return False


class TDPrefsPromptDialog(FramelessDialog):
    """First-run prompt: tick what ALPACKA should set, or ignore it all.

    `decision` is ``"apply"``, ``"ignore"``, or ``None`` when the dialog
    was closed without choosing (it asks again next start).
    """

    def __init__(
        self,
        specs: list[td_prefs.PrefSpec],
        state: td_prefs.State,
        current: dict[str, str],
        pref_file: Path,
        *,
        td_running: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("TouchDesigner Preferences", parent)
        self.resize(820, 0)
        self.decision: str | None = None
        layout = self.content_layout

        intro = QLabel(
            "ALPACKA can set these TouchDesigner preferences for you. It sets "
            "the ticked ones before each TD launch and tells you if it has to "
            "set one back. Your other TD preferences are left as they are, and "
            "a copy of the original file is kept next to it."
        )
        intro.setObjectName("dialogMessage")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        path = QLabel(str(pref_file))
        path.setObjectName("pathPreview")
        path.setWordWrap(True)
        layout.addWidget(path)

        self.table = TDPrefsTable(specs, mode="prompt")
        self.table.set_values(state, current)
        layout.addWidget(self.table)

        warning = td_prefs.python_path_warning(current)
        if warning:
            label = QLabel(warning)
            label.setObjectName("tdPrefsWarning")
            label.setWordWrap(True)
            layout.addWidget(label)

        if td_running:
            running = QLabel(
                "TouchDesigner is running, so ALPACKA will write these the next "
                "time it launches TD with no other TD open."
            )
            running.setObjectName("templateDescription")
            running.setWordWrap(True)
            layout.addWidget(running)

        hint = QLabel("You can change these any time in Settings.")
        hint.setObjectName("templateDescription")
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        ignore = QPushButton("Ignore")
        ignore.clicked.connect(self._on_ignore)
        buttons.addWidget(ignore)
        apply = QPushButton("Apply Selected")
        apply.setObjectName("primaryButton")
        apply.setDefault(True)
        apply.clicked.connect(self._on_apply)
        buttons.addWidget(apply)
        layout.addLayout(buttons)

    def showEvent(self, event) -> None:  # type: ignore[override]
        # A top-level window sizes itself from its layout's minimum size,
        # which ignores the extra height wrapped labels need at the actual
        # width — so the table got squeezed and clipped. Grow to fit.
        super().showEvent(event)
        layout = self.layout()
        if layout is not None and layout.hasHeightForWidth():
            needed = layout.totalHeightForWidth(self.width())
            if needed > self.height():
                self.resize(self.width(), needed)

    def _on_ignore(self) -> None:
        self.decision = "ignore"
        self.reject()

    def _on_apply(self) -> None:
        self.decision = "apply"
        self.accept()
