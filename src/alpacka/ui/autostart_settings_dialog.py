"""Autostart & monitoring settings dialog.

Opened from the zap button in a project card's title row. Edits the
launcher-owned auto-launch / auto-relaunch fields on `alpacka.yml`:

- ``launch_on_startup`` — UI label "Auto Launch (on launcher open)".
  Master switch for the entire feature; when off, nothing auto-starts
  and (per the UI invariant) nothing auto-relaunches either.
- ``reopen_on_crash`` — UI label "Auto Relaunch on Close (or freeze)".
  Sub-toggle under Auto Launch. When both are on, the launcher watches
  the running process and relaunches on crash or freeze.
- ``freeze_timeout_seconds`` — how long a window must be unresponsive
  before it's considered hung and force-killed.
- ``startup_grace_seconds`` — skip hang detection for this long after
  launch (TouchDesigner takes ~60s to boot).
- ``relaunch_delay_seconds`` — cooldown between a crash/kill and the
  relaunch attempt.
- ``max_retries_enabled`` + ``max_retries`` — cap on consecutive
  auto-relaunches (off → retry forever; counter resets on manual launch).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import approval, library, project
from . import frameless
from .frameless import FramelessDialog


class AutostartSettingsDialog(FramelessDialog):
    def __init__(
        self,
        entry: library.LibraryEntry,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Auto Launch & Relaunch", parent)
        self.resize(480, 0)
        self._entry = entry
        self._saved = False
        # (label_widget, field_widget) pairs gated by Auto Relaunch
        # (which is itself gated by Auto Launch). Form labels are
        # tracked alongside their field so both gray out together.
        self._relaunch_dependent: list[tuple[QLabel, QWidget]] = []
        # (label_widget, field_widget) pairs gated by the Retry Cap
        # checkbox (on top of Auto Relaunch + Auto Launch).
        self._retry_cap_dependent: list[tuple[QLabel, QWidget]] = []
        # Fields set in alpacka.local.yml: shown but not editable here.
        self._locked: dict[str, QWidget] = {}
        self._build_ui()

    @property
    def saved(self) -> bool:
        return self._saved

    def _build_ui(self) -> None:
        layout = self.content_layout

        proj = self._entry.project
        if proj is None:
            layout.addWidget(
                QLabel(
                    "alpacka.yml is missing or unreadable. "
                    "There's nothing to edit."
                )
            )
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self.reject)
            layout.addWidget(buttons)
            return

        # --- Auto Launch master -------------------------------------
        self.launch_startup_check = QCheckBox("Auto Launch (on launcher open)")
        self.launch_startup_check.setChecked(bool(proj.launch_on_startup))
        self.launch_startup_check.setToolTip(
            "Launches this project automatically when the launcher opens. "
            "Required for Auto Relaunch to apply."
        )
        layout.addWidget(self.launch_startup_check)

        al_help = QLabel(
            "Launches this project automatically when the launcher opens."
        )
        al_help.setWordWrap(True)
        al_help.setObjectName("pathPreview")
        layout.addWidget(al_help)

        layout.addSpacing(10)

        # --- Auto Relaunch subgroup (indented under Auto Launch) ----
        sub_container = QWidget()
        sub_layout = QVBoxLayout(sub_container)
        sub_layout.setContentsMargins(16, 0, 0, 0)
        sub_layout.setSpacing(8)

        self.auto_relaunch_check = QCheckBox(
            "Auto Relaunch on Close (or freeze)"
        )
        self.auto_relaunch_check.setChecked(bool(proj.reopen_on_crash))
        self.auto_relaunch_check.setToolTip(
            "Watch the running process and relaunch it automatically on "
            "crash or freeze. Requires Auto Launch."
        )
        sub_layout.addWidget(self.auto_relaunch_check)

        self._auto_relaunch_help = QLabel(
            "Watches the process and relaunches it if it crashes or its "
            "window goes unresponsive. Freeze detection is Windows-only; "
            "crash detection works everywhere."
        )
        self._auto_relaunch_help.setWordWrap(True)
        self._auto_relaunch_help.setObjectName("pathPreview")
        sub_layout.addWidget(self._auto_relaunch_help)

        sub_layout.addSpacing(2)

        timing_form = QFormLayout()
        timing_form.setSpacing(8)
        timing_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.startup_grace_spin = QSpinBox()
        self.startup_grace_spin.setRange(0, 3600)
        self.startup_grace_spin.setValue(int(proj.startup_grace_seconds))
        self.startup_grace_spin.setToolTip(
            "Skip freeze detection for this long after launch. TouchDesigner "
            "can take ~60 seconds to boot and often appears unresponsive "
            "during that window."
        )
        self._add_relaunch_row(
            timing_form, "Startup Grace (sec):", self.startup_grace_spin
        )

        self.freeze_timeout_spin = QSpinBox()
        self.freeze_timeout_spin.setRange(1, 3600)
        self.freeze_timeout_spin.setValue(int(proj.freeze_timeout_seconds))
        self.freeze_timeout_spin.setToolTip(
            "Force-kill and relaunch if the main window is unresponsive "
            "for this long. Windows only."
        )
        self._add_relaunch_row(
            timing_form, "Freeze Timeout (sec):", self.freeze_timeout_spin
        )

        self.relaunch_delay_spin = QSpinBox()
        self.relaunch_delay_spin.setRange(0, 3600)
        self.relaunch_delay_spin.setValue(int(proj.relaunch_delay_seconds))
        self.relaunch_delay_spin.setToolTip(
            "Wait this long after a crash or forced kill before attempting "
            "to relaunch. Keeps tight crash-loops at bay."
        )
        self._add_relaunch_row(
            timing_form, "Relaunch Delay (sec):", self.relaunch_delay_spin
        )

        sub_layout.addLayout(timing_form)

        sub_layout.addSpacing(4)
        self.retry_cap_check = QCheckBox("Limit total retries")
        self.retry_cap_check.setChecked(bool(proj.max_retries_enabled))
        self.retry_cap_check.setToolTip(
            "When off, retry forever. When on, stop auto-relaunching after "
            "the configured number of consecutive attempts — the counter "
            "resets when you manually launch the project."
        )
        sub_layout.addWidget(self.retry_cap_check)
        # The retry-cap checkbox itself is gated by Auto Relaunch, so
        # track it alongside the rest of the relaunch-gated controls.
        # It has no form label, so pair it with a hidden placeholder.
        self._relaunch_dependent.append((QLabel(), self.retry_cap_check))

        retry_form = QFormLayout()
        retry_form.setSpacing(8)
        retry_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.max_retries_spin = QSpinBox()
        self.max_retries_spin.setRange(1, 100)
        self.max_retries_spin.setValue(int(proj.max_retries))
        self._add_retry_cap_row(
            retry_form, "Max Retries:", self.max_retries_spin
        )
        sub_layout.addLayout(retry_form)

        layout.addWidget(sub_container)

        owned = project.local_overrides(proj).top
        fields = {
            "launch_on_startup": self.launch_startup_check,
            "reopen_on_crash": self.auto_relaunch_check,
            "startup_grace_seconds": self.startup_grace_spin,
            "freeze_timeout_seconds": self.freeze_timeout_spin,
            "relaunch_delay_seconds": self.relaunch_delay_spin,
            "max_retries_enabled": self.retry_cap_check,
            "max_retries": self.max_retries_spin,
        }
        self._locked = {k: w for k, w in fields.items() if k in owned}
        if self._locked:
            note = QLabel(
                "Greyed-out settings come from alpacka.local.yml on this "
                "machine. Change them in that file."
            )
            note.setWordWrap(True)
            note.setObjectName("pathPreview")
            layout.addWidget(note)

        # --- Gating hooks -------------------------------------------
        self.launch_startup_check.toggled.connect(self._update_enabled_states)
        self.auto_relaunch_check.toggled.connect(self._update_enabled_states)
        self.retry_cap_check.toggled.connect(self._update_enabled_states)
        self._update_enabled_states()

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

    def _add_relaunch_row(
        self, form: QFormLayout, label_text: str, widget: QWidget
    ) -> None:
        label = QLabel(label_text)
        form.addRow(label, widget)
        self._relaunch_dependent.append((label, widget))

    def _add_retry_cap_row(
        self, form: QFormLayout, label_text: str, widget: QWidget
    ) -> None:
        label = QLabel(label_text)
        form.addRow(label, widget)
        self._retry_cap_dependent.append((label, widget))

    def _update_enabled_states(self) -> None:
        auto_launch = self.launch_startup_check.isChecked()
        # Auto Relaunch needs BOTH its own checkbox AND Auto Launch.
        auto_relaunch = self.auto_relaunch_check.isChecked() and auto_launch
        # Retry Cap needs auto_relaunch AND its own checkbox.
        retry_cap = self.retry_cap_check.isChecked() and auto_relaunch

        # Auto Relaunch checkbox + help gray with Auto Launch.
        self.auto_relaunch_check.setEnabled(auto_launch)
        self._auto_relaunch_help.setEnabled(auto_launch)

        # Monitoring fields gray with Auto Relaunch.
        for label, widget in self._relaunch_dependent:
            label.setEnabled(auto_relaunch)
            widget.setEnabled(auto_relaunch)

        # Max Retries grays with the Retry Cap toggle (which already
        # requires Auto Relaunch + Auto Launch).
        for label, widget in self._retry_cap_dependent:
            label.setEnabled(retry_cap)
            widget.setEnabled(retry_cap)

        for widget in self._locked.values():
            widget.setEnabled(False)
            widget.setToolTip("Set in alpacka.local.yml")

    def _on_save(self) -> None:
        # Reload from disk to preserve any AcroCore-owned changes that may
        # have landed between opening the dialog and Save.
        proj = project.load(self._entry.path)
        if proj is None:
            frameless.critical(
                self,
                "Could not save",
                f"alpacka.yml is missing or unreadable at {self._entry.path}",
            )
            return

        approved_before = approval.was_approved(self._entry.row_id, proj)
        proj.launch_on_startup = self.launch_startup_check.isChecked()
        proj.reopen_on_crash = self.auto_relaunch_check.isChecked()
        proj.startup_grace_seconds = int(self.startup_grace_spin.value())
        proj.freeze_timeout_seconds = int(self.freeze_timeout_spin.value())
        proj.relaunch_delay_seconds = int(self.relaunch_delay_spin.value())
        proj.max_retries_enabled = self.retry_cap_check.isChecked()
        proj.max_retries = int(self.max_retries_spin.value())

        try:
            project.save(proj)
        except OSError as exc:
            frameless.critical(self, "Could not save", str(exc))
            return
        approval.carry_forward(self._entry.row_id, approved_before)

        self._saved = True
        self.accept()
