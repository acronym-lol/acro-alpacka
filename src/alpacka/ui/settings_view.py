"""Settings view — TD install list, default install picker, manual add."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, external, process_tracker, settings, td_install, td_prefs, updater
from . import frameless, update_dialog
from .frameless import FramelessDialog
from .td_prefs_dialog import TDPrefsTable


class _ManualTDDialog(FramelessDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Add TD Install Manually", parent)
        self.resize(480, 0)

        layout = self.content_layout

        form = QFormLayout()
        form.setSpacing(8)

        self.version_edit = QLineEdit()
        self.version_edit.setPlaceholderText("2025.32280")
        form.addRow("Version (year.build):", self.version_edit)

        path_row = QWidget()
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Path to TouchDesigner.exe / .app...")
        path_layout.addWidget(self.path_edit, stretch=1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        path_layout.addWidget(browse)
        form.addRow("Path:", path_row)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Pick TouchDesigner binary / .app", str(Path.home())
        )
        if chosen:
            self.path_edit.setText(chosen)

    def _on_ok(self) -> None:
        if not self.version_edit.text().strip():
            frameless.warning(self, "Missing field", "Version is required.")
            return
        if not self.path_edit.text().strip():
            frameless.warning(self, "Missing field", "Path is required.")
            return
        self.accept()

    def result_install(self) -> settings.TDInstall:
        return settings.TDInstall(
            version=self.version_edit.text().strip(),
            major="099",
            path=self.path_edit.text().strip(),
        )


class SettingsView(QWidget):
    """Emits `settings_changed` when the user modifies settings."""

    settings_changed = Signal()

    def __init__(self, app_settings: settings.Settings) -> None:
        super().__init__()
        self._settings = app_settings
        # Cache the most recent update-check result so the status label
        # keeps its message after the background check completes.
        self._latest_update: updater.UpdateInfo | None = None
        self._check_in_progress: bool = False
        self._build_ui()
        self.refresh()
        self.refresh_updates_status()
        self.refresh_td_prefs()

    @property
    def current_settings(self) -> settings.Settings:
        return self._settings

    def _build_ui(self) -> None:
        # The page outgrew the window once TD preferences landed, so the
        # whole thing scrolls (same pattern as the New Project page).
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        body.setObjectName("settingsContent")
        scroll.setWidget(body)

        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        heading = QLabel("Settings")
        heading.setObjectName("sectionHeading")
        layout.addWidget(heading)

        section_title = QLabel("TouchDesigner Installations")
        section_title.setObjectName("subHeading")
        layout.addWidget(section_title)

        toolbar = QHBoxLayout()
        rescan_btn = QPushButton("Rescan")
        rescan_btn.clicked.connect(self._on_rescan)
        toolbar.addWidget(rescan_btn)

        add_btn = QPushButton("Add Manually...")
        add_btn.clicked.connect(self._on_add_manual)
        toolbar.addWidget(add_btn)

        set_default_btn = QPushButton("Set as Default")
        set_default_btn.clicked.connect(self._on_set_default)
        toolbar.addWidget(set_default_btn)

        remove_btn = QPushButton("Remove Manual Entry")
        remove_btn.clicked.connect(self._on_remove)
        toolbar.addWidget(remove_btn)

        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setMinimumHeight(140)
        layout.addWidget(self.list_widget)

        self._hint = QLabel(
            "The launcher scans the Windows registry / /Applications on every startup. "
            "Manually-added entries are stored in settings.json and preserved across rescans."
        )
        self._hint.setObjectName("templateDescription")
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        self._build_td_prefs_section(layout)

        # ------------------------------------------------------------------
        # New Project Defaults
        # ------------------------------------------------------------------
        defaults_title = QLabel("New Project Defaults")
        defaults_title.setObjectName("subHeading")
        layout.addWidget(defaults_title)

        parent_row = QHBoxLayout()
        parent_label = QLabel("Default parent folder:")
        parent_row.addWidget(parent_label)
        self._default_parent_edit = QLineEdit(self._settings.default_parent_folder)
        self._default_parent_edit.setPlaceholderText(
            "Pre-fill the New Project dialog (optional)"
        )
        self._default_parent_edit.editingFinished.connect(
            self._on_default_parent_changed
        )
        parent_row.addWidget(self._default_parent_edit, stretch=1)
        parent_browse = QPushButton("Browse...")
        parent_browse.clicked.connect(self._on_browse_default_parent)
        parent_row.addWidget(parent_browse)
        parent_clear = QPushButton("Clear")
        parent_clear.clicked.connect(self._on_clear_default_parent)
        parent_row.addWidget(parent_clear)
        layout.addLayout(parent_row)

        defaults_hint = QLabel(
            "When set, this path pre-fills the Parent folder field in the New "
            "Project dialog. You can still browse to a different folder per "
            "project."
        )
        defaults_hint.setObjectName("templateDescription")
        defaults_hint.setWordWrap(True)
        layout.addWidget(defaults_hint)

        # ------------------------------------------------------------------
        # Application Updates
        # ------------------------------------------------------------------
        updates_title = QLabel("Application Updates")
        updates_title.setObjectName("subHeading")
        layout.addWidget(updates_title)

        update_row = QHBoxLayout()
        self._check_updates_btn = QPushButton("Check for Updates Now")
        self._check_updates_btn.clicked.connect(self._on_check_updates_now)
        update_row.addWidget(self._check_updates_btn)

        # The installed Windows build updates itself; everything else
        # (portable, macOS, source) downloads from the release page.
        self._can_install_update = updater.can_install_in_place()
        self._install_update_btn = QPushButton("Update and Restart")
        self._install_update_btn.setObjectName("primaryButton")
        self._install_update_btn.clicked.connect(self._on_install_update)
        self._install_update_btn.setVisible(False)
        update_row.addWidget(self._install_update_btn)

        self._open_release_btn = QPushButton("Open Release Page")
        if not self._can_install_update:
            self._open_release_btn.setObjectName("primaryButton")
        self._open_release_btn.clicked.connect(self._on_open_release_page)
        self._open_release_btn.setVisible(False)
        update_row.addWidget(self._open_release_btn)

        self._update_status_label = QLabel()
        self._update_status_label.setObjectName("templateDescription")
        self._update_status_label.setWordWrap(True)
        update_row.addWidget(self._update_status_label, stretch=1)
        layout.addLayout(update_row)

        self._auto_check_cb = QCheckBox("Automatically check for updates on startup")
        self._auto_check_cb.setChecked(self._settings.auto_update_check)
        self._auto_check_cb.toggled.connect(self._on_auto_check_toggled)
        layout.addWidget(self._auto_check_cb)

        # "Skipped: vX.Y.Z [Clear]" — only visible when the user has
        # explicitly skipped a version via the startup prompt.
        skipped_row = QHBoxLayout()
        self._skipped_label = QLabel()
        self._skipped_label.setObjectName("templateDescription")
        skipped_row.addWidget(self._skipped_label)
        self._clear_skipped_btn = QPushButton("Clear")
        self._clear_skipped_btn.clicked.connect(self._on_clear_skipped)
        skipped_row.addWidget(self._clear_skipped_btn)
        skipped_row.addStretch(1)
        self._skipped_container = QWidget()
        self._skipped_container.setLayout(skipped_row)
        self._skipped_container.setVisible(False)
        layout.addWidget(self._skipped_container)
        layout.addStretch(1)

    def _build_td_prefs_section(self, layout: QVBoxLayout) -> None:
        title = QLabel("TouchDesigner Preferences")
        title.setObjectName("subHeading")
        layout.addWidget(title)

        intro = QLabel(
            "ALPACKA can manage a few preferences in TouchDesigner's machine-wide "
            "pref.txt. It sets the ticked ones before each TD launch and sets them "
            "back if they change. Your other TD preferences are left alone, and a "
            "copy of the original file is kept as pref.txt.alpacka-backup."
        )
        intro.setObjectName("templateDescription")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        path_row = QHBoxLayout()
        self._td_prefs_path_label = QLabel()
        self._td_prefs_path_label.setObjectName("pathPreview")
        self._td_prefs_path_label.setWordWrap(True)
        path_row.addWidget(self._td_prefs_path_label, stretch=1)
        self._td_prefs_open_btn = QPushButton("Open Folder")
        self._td_prefs_open_btn.clicked.connect(self._on_td_prefs_open_folder)
        path_row.addWidget(self._td_prefs_open_btn)
        layout.addLayout(path_row)

        self._td_prefs_status = QLabel()
        self._td_prefs_status.setObjectName("templateDescription")
        self._td_prefs_status.setWordWrap(True)
        layout.addWidget(self._td_prefs_status)

        self._td_prefs_warning = QLabel()
        self._td_prefs_warning.setObjectName("tdPrefsWarning")
        self._td_prefs_warning.setWordWrap(True)
        self._td_prefs_warning.setVisible(False)
        layout.addWidget(self._td_prefs_warning)

        self._td_prefs_table = TDPrefsTable(list(td_prefs.RECOMMENDED), mode="settings")
        self._td_prefs_table.changed.connect(self._update_td_prefs_buttons)
        layout.addWidget(self._td_prefs_table)

        buttons = QHBoxLayout()
        self._td_prefs_apply_btn = QPushButton("Apply")
        self._td_prefs_apply_btn.setObjectName("primaryButton")
        self._td_prefs_apply_btn.clicked.connect(self._on_td_prefs_apply)
        buttons.addWidget(self._td_prefs_apply_btn)
        self._td_prefs_revert_btn = QPushButton("Revert All")
        self._td_prefs_revert_btn.clicked.connect(self._on_td_prefs_revert)
        buttons.addWidget(self._td_prefs_revert_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    def refresh(self) -> None:
        self.list_widget.clear()
        for inst in self._settings.td_installs:
            label_parts = [f"TouchDesigner {inst.version}"]
            if inst.version == self._settings.default_td_install:
                label_parts.append("(default)")
            path_exists = Path(inst.path).exists() if inst.path else False
            if not path_exists:
                label_parts.append("⚠ path missing")
            item = QListWidgetItem(f"{'  '.join(label_parts)}\n  {inst.path}")
            item.setData(Qt.ItemDataRole.UserRole, inst.version)
            self.list_widget.addItem(item)

    def _selected_install(self) -> settings.TDInstall | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        version = item.data(Qt.ItemDataRole.UserRole)
        for i in self._settings.td_installs:
            if i.version == version:
                return i
        return None

    def _on_rescan(self) -> None:
        detected = td_install.detect()
        # Merge: detected ones overwrite matching version entries; manually-added
        # entries (versions not seen by the scanner) are preserved.
        by_version = {i.version: i for i in self._settings.td_installs}
        for d in detected:
            by_version[d.version] = d
        self._settings.td_installs = list(by_version.values())
        self._settings.td_installs.sort(key=lambda i: i.version, reverse=True)

        # Ensure a sensible default.
        versions = {i.version for i in self._settings.td_installs}
        if self._settings.default_td_install not in versions:
            self._settings.default_td_install = (
                self._settings.td_installs[0].version if self._settings.td_installs else ""
            )

        settings.save(self._settings)
        self.refresh()
        self.settings_changed.emit()

    def _on_add_manual(self) -> None:
        dialog = _ManualTDDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_install = dialog.result_install()
        existing = [i for i in self._settings.td_installs if i.version != new_install.version]
        existing.append(new_install)
        existing.sort(key=lambda i: i.version, reverse=True)
        self._settings.td_installs = existing
        if not self._settings.default_td_install:
            self._settings.default_td_install = new_install.version
        settings.save(self._settings)
        self.refresh()
        self.settings_changed.emit()

    def _on_set_default(self) -> None:
        install = self._selected_install()
        if install is None:
            frameless.info(self, "Set as Default", "Pick a TD install first.")
            return
        self._settings.default_td_install = install.version
        settings.save(self._settings)
        self.refresh()
        self.settings_changed.emit()

    def _on_remove(self) -> None:
        install = self._selected_install()
        if install is None:
            frameless.info(self, "Remove", "Pick a TD install first.")
            return
        # Only allow removing entries that are NOT currently detected by a rescan —
        # otherwise they'd just come back.
        detected_versions = {d.version for d in td_install.detect()}
        if install.version in detected_versions:
            frameless.info(
                self,
                "Remove",
                "This install was auto-detected. Uninstall it from the OS to remove it from the list.",
            )
            return
        self._settings.td_installs = [
            i for i in self._settings.td_installs if i.version != install.version
        ]
        if self._settings.default_td_install == install.version:
            self._settings.default_td_install = (
                self._settings.td_installs[0].version if self._settings.td_installs else ""
            )
        settings.save(self._settings)
        self.refresh()
        self.settings_changed.emit()

    # --- Updates section --------------------------------------------------

    def refresh_updates_status(self) -> None:
        """Re-render the Updates section from current settings + cache.

        Called at init, after the startup check completes, and after any
        user-driven state change (manual check, auto-check toggle, clear
        skipped version).
        """
        available = not self._check_in_progress and self._latest_update is not None
        if self._check_in_progress:
            self._update_status_label.setText("Checking…")
        elif self._latest_update is not None:
            self._update_status_label.setText(
                f"ALPACKA {self._latest_update.version} is available "
                f"(you're on {__version__})."
            )
        else:
            last = self._format_last_check(self._settings.last_update_check)
            self._update_status_label.setText(
                f"You're on ALPACKA {__version__} — up to date.  Last checked: {last}."
            )
        self._open_release_btn.setVisible(available)
        self._install_update_btn.setVisible(available and self._can_install_update)

        self._auto_check_cb.setChecked(self._settings.auto_update_check)

        skipped = self._settings.skipped_version
        if skipped:
            self._skipped_label.setText(f"Skipped version: {skipped}")
            self._skipped_container.setVisible(True)
        else:
            self._skipped_container.setVisible(False)

    @staticmethod
    def _format_last_check(ts: float) -> str:
        if not ts:
            return "never"
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%Y-%m-%d %H:%M")

    def _on_check_updates_now(self) -> None:
        if self._check_in_progress:
            return
        self._check_in_progress = True
        self._check_updates_btn.setEnabled(False)
        self.refresh_updates_status()
        updater.start_check(self._on_manual_update_result)

    def _on_manual_update_result(self, info: "updater.UpdateInfo | None") -> None:
        self._check_in_progress = False
        self._check_updates_btn.setEnabled(True)
        self._latest_update = info
        self._settings.last_update_check = time.time()
        settings.save(self._settings)
        self.refresh_updates_status()

    def set_latest_update(self, info: "updater.UpdateInfo | None") -> None:
        """Show the result of the startup check here too."""
        self._latest_update = info
        self.refresh_updates_status()

    def _on_open_release_page(self) -> None:
        if self._latest_update is None:
            return
        QDesktopServices.openUrl(QUrl(updater.safe_release_url(self._latest_update.release_page)))

    def _on_install_update(self) -> None:
        if self._latest_update is None:
            return
        update_dialog.run_update(self.window(), self._latest_update)

    def _on_auto_check_toggled(self, checked: bool) -> None:
        self._settings.auto_update_check = checked
        settings.save(self._settings)

    def _on_clear_skipped(self) -> None:
        self._settings.skipped_version = ""
        settings.save(self._settings)
        self.refresh_updates_status()

    # --- New Project defaults --------------------------------------------

    def _on_default_parent_changed(self) -> None:
        """Persist the typed-in default parent folder.

        Called via `editingFinished` (focus loss / Enter) rather than
        textChanged so we don't write on every keystroke. Whitespace is
        stripped; an empty string clears the default.
        """
        value = self._default_parent_edit.text().strip()
        if value == self._settings.default_parent_folder:
            return
        self._settings.default_parent_folder = value
        settings.save(self._settings)
        self.settings_changed.emit()

    def _on_browse_default_parent(self) -> None:
        start = self._settings.default_parent_folder or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose default parent folder", start
        )
        if not chosen:
            return
        self._default_parent_edit.setText(chosen)
        self._on_default_parent_changed()

    def _on_clear_default_parent(self) -> None:
        if not self._settings.default_parent_folder:
            return
        self._default_parent_edit.clear()
        self._settings.default_parent_folder = ""
        settings.save(self._settings)
        self.settings_changed.emit()

    # --- TouchDesigner preferences -----------------------------------------

    def td_majors(self) -> list[str]:
        installs = list(self._settings.td_installs)
        installs.sort(key=lambda i: i.version != self._settings.default_td_install)
        # Every current TD build uses major "099" (td_install._DEFAULT_MAJOR).
        return td_prefs.majors_for(installs) or ["099"]

    def refresh_td_prefs(self, *, keep_edits: bool = False) -> None:
        """Re-read pref.txt + saved decisions into the section.

        Called when the Settings page is shown, after Apply / Revert, and
        (with `keep_edits=True`) after every launch-time reconcile.
        """
        path = td_prefs.pref_path(self.td_majors()[0])
        available = path is not None
        self._td_prefs_table.setVisible(available)
        self._td_prefs_open_btn.setVisible(available)
        self._td_prefs_revert_btn.setVisible(available)
        self._td_prefs_apply_btn.setVisible(available)
        if path is None:
            self._td_prefs_path_label.setText(
                "TouchDesigner's preferences file isn't available on this system "
                "yet. On macOS it appears after TD has been run once."
            )
            self._td_prefs_status.setText("")
            self._td_prefs_warning.setVisible(False)
            return

        current = td_prefs.read_values(path)
        state = td_prefs.load_state()
        self._td_prefs_path_label.setText(str(path))
        self._td_prefs_table.set_values(
            state, current, td_prefs.peek_file_state(state, path), keep_edits=keep_edits
        )
        self._td_prefs_status.setText(self._format_td_prefs_status(state.last_result))
        warning = td_prefs.python_path_warning(current)
        self._td_prefs_warning.setText(warning or "")
        self._td_prefs_warning.setVisible(bool(warning))
        self._td_prefs_revert_btn.setEnabled(bool(state.managed))
        self._update_td_prefs_buttons()

    def _update_td_prefs_buttons(self) -> None:
        self._td_prefs_apply_btn.setEnabled(self._td_prefs_table.is_dirty())

    @staticmethod
    def _format_td_prefs_status(result: "td_prefs.Result | None") -> str:
        if result is None:
            return "ALPACKA hasn't changed any TouchDesigner preferences yet."
        try:
            when = datetime.fromisoformat(result.at).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            when = "?"
        changes = "; ".join(c.describe() for c in result.changes)
        if result.status == "applied":
            return f"Last applied {when}: {changes}."
        if result.status == "up_to_date":
            return f"Up to date (checked {when})."
        if result.status == "waiting":
            return (
                f"Waiting since {when}: TouchDesigner was running, so ALPACKA will "
                f"write these at the next TD launch: {changes}."
            )
        if result.status == "error":
            return f"Couldn't update pref.txt ({when}): {result.message}"
        return ""

    def _reconcile_td_prefs(self) -> None:
        running = process_tracker.any_td_running()
        for major in self.td_majors():
            td_prefs.reconcile(major, td_running=running, source="ui")

    def _on_td_prefs_apply(self) -> None:
        accept, decline = self._td_prefs_table.pending_changes()
        td_prefs.decide(accept, decline)
        self._reconcile_td_prefs()
        self.refresh_td_prefs()

    def _on_td_prefs_revert(self) -> None:
        state = td_prefs.load_state()
        if not state.managed:
            return
        if not frameless.question(
            self,
            "Revert TouchDesigner preferences",
            "Put back the values these preferences had before ALPACKA changed "
            "them, and stop managing them?",
            yes="Revert",
            no="Cancel",
        ):
            return
        td_prefs.revert_all()
        self._reconcile_td_prefs()
        self.refresh_td_prefs()

    def _on_td_prefs_open_folder(self) -> None:
        path = td_prefs.pref_path(self.td_majors()[0])
        if path is None:
            return
        try:
            external.open_folder(path.parent)
        except external.ExternalToolError as exc:
            frameless.warning(self, "Open Folder", str(exc))
