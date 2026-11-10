"""Edit Details view — full-page editor for a project's launcher-owned fields.

Mounted in the main window's content stack (alongside Library / Templates /
Settings) instead of opening as a modal dialog. The page has a sticky
header with Save / Cancel; the editable content lives below in a vertical
QScrollArea so long projects (lots of variables, big description, etc.)
can still be reached.

Launcher-owned fields are editable (name, description, tags, variables).
AcroCore-owned and creation metadata (template, td_version, td_major, toe,
created, folder) are shown read-only for reference. The thumbnail at
`<project>/.launcher-thumb.png` is the source of truth and gets picked
up by the card automatically.
"""
from __future__ import annotations

import shlex
import shutil
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import approval, kinds, library, process_tracker, project
from . import frameless
from .tag_picker_dialog import TagChipBar, TagPickerDialog

_ICONS_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"

_THUMB_PREVIEW_W = 160
_THUMB_PREVIEW_H = 90
_IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp)"

# Maximum reading-column width for the form area. On wider windows the
# content stays centered with the extra space split as side margins;
# narrower windows let the column shrink to fit.
_CONTENT_MAX_WIDTH = 880


class _CenteredColumnViewport(QWidget):
    """Hosts a single child widget centered within a max-width column.

    Qt's QHBoxLayout + addStretch idiom for "centered max-width content"
    is fragile — the widget's stretch factor in the layout has to
    out-compete the side spacers, and even then it only reaches maxWidth
    when the viewport is far wider than 3× maxWidth. This subclass
    sidesteps the layout math: on every resize we just compute the
    target side padding (viewport - maxWidth, halved) and apply it as
    contentsMargins. The child widget then fills whatever's left over,
    which is exactly maxWidth on wide windows and the full viewport on
    narrow ones.
    """

    def __init__(self, content: QWidget, max_width: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._max_width = max_width
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(0)
        self._row.addWidget(content)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        excess = self.width() - self._max_width
        margin = max(0, excess // 2)
        self._row.setContentsMargins(margin, 0, margin, 0)

_VAR_TYPES = ("string", "int", "bool", "path", "float")
_VAR_TYPE_COL_WIDTH = 96

# Display labels for output_mode values — used by the output-mode
# picker's "Default for kind (...)" line and shared with NewProjectView.
_OUTPUT_MODE_LABELS = {
    "console": "Console",
    "inline": "Inline",
    "none": "None",
}

# Pairs of (display label, raw TOUCH_APP_LOG_LEVEL value) for the TD
# launch section's log-level dropdown. Empty string ⇒ unset / TD default.
_TD_LOG_LEVELS: tuple[tuple[str, str], ...] = (
    ("Default (unset)", ""),
    ("Off", "Off"),
    ("Error", "Error"),
    ("Warning", "Warning"),
    ("Info", "Info"),
    ("Debug", "Debug"),
)


# Per-kind metadata for the Run section. Each entry says which primary
# field to expose (label + placeholder) and where its value lives:
#
#   - `kind_data_key`: stored under `proj.kind_data[<kind>][<key>]` —
#     used by Python (`entry`) and Executable (`path`).
#   - `command`: stored on the default run_config's `command` field —
#     used by NodeJS / Shell / Docker-compose / Generic, where the
#     "primary" launch verb is the run-config command itself.
#
# Args (the second field) always go to the default run_config's `args`,
# regardless of where the primary value lives. TouchDesigner has no
# entry — its argv is the .toe path — so it's omitted; the Run section
# is hidden for TD projects.
_RUN_FIELD_SPECS: dict[str, dict[str, str]] = {
    "python": {
        "label": "Entry:",
        "placeholder": "main.py",
        "kind_data_key": "entry",
        "hint": "Path to a .py file relative to the project, or a module name (e.g. `mypackage.cli`). Empty = error at launch.",
    },
    "nodejs": {
        "label": "Script:",
        "placeholder": "Auto: dev (or start) from package.json",
        "command": "1",
        "hint": "An npm script name to run. Empty = auto-detect from package.json.",
    },
    "shell": {
        "label": "Command:",
        "placeholder": "./run.sh",
        "command": "1",
        "hint": "Command to execute via the shell.",
    },
    "docker-compose": {
        "label": "Compose command:",
        "placeholder": "up (default)",
        "command": "1",
        "hint": "Compose subcommand and flags. Empty = `up`.",
    },
    "executable": {
        "label": "Executable:",
        "placeholder": "build/app.exe",
        "kind_data_key": "path",
        "hint": "Path to the binary, relative to the project or absolute.",
    },
    "generic": {
        "label": "Command:",
        "placeholder": "./your-binary --flag",
        "command": "1",
        "hint": "Anything you'd type into a shell — supports {VAR} substitution from this project's variables.",
    },
}


_LOCAL_TOOLTIP = "Set in alpacka.local.yml — change it in that file."


def _lock(widget: QWidget) -> None:
    """Show a value from alpacka.local.yml read-only."""
    widget.setEnabled(False)
    widget.setToolTip(_LOCAL_TOOLTIP)


def _ensure_default_run_config(
    proj: project.Project, default_name: str
) -> dict[str, Any]:
    """Find or create the default run_config dict on `proj.run_configs`.

    `default_name` comes from the kind handler's `default_run_config(proj).name`
    (typically `"default"`). The dict is mutated in place by callers so
    `proj.run_configs` reflects updates without a re-assignment.
    """
    for cfg in proj.run_configs:
        if isinstance(cfg, dict) and cfg.get("name") == default_name:
            return cfg
    new_cfg: dict[str, Any] = {"name": default_name}
    proj.run_configs.append(new_cfg)
    return new_cfg


def _prune_empty_run_configs(proj: project.Project) -> None:
    """Drop any run_config dict that's just `{name: ...}` with nothing else.

    Keeps `alpacka.yml` minimal — a project that uses the kind's defaults
    shouldn't have a stub `run_configs` entry pinning that.
    """
    proj.run_configs = [
        cfg for cfg in proj.run_configs
        if not (
            isinstance(cfg, dict)
            and len(cfg) == 1
            and "name" in cfg
        )
    ]


class EditDetailsView(QWidget):
    """In-stack page for editing a single project's metadata.

    Lifecycle: `set_entry(entry)` populates all fields from the project;
    Save persists and emits `saved(row_id)`; Cancel emits `cancelled()`.
    The MainWindow listens for both signals to navigate back to the
    library view.
    """

    saved = Signal(int)
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("editDetailsView")
        self._entry: library.LibraryEntry | None = None

        # Long-lived widgets that survive across `set_entry` calls.
        self._title_label: QLabel | None = None
        self._content_widget: QWidget | None = None

        # Per-entry widgets — recreated each time `set_entry` is called.
        self.name_edit: QLineEdit | None = None
        self.description_edit: QTextEdit | None = None
        self.tag_bar: TagChipBar | None = None
        self._run_primary_edit: QLineEdit | None = None
        self._run_args_edit: QLineEdit | None = None
        self._var_rows: list[_VarRow] = []
        self._var_rows_container: QWidget | None = None
        self._var_rows_layout: QVBoxLayout | None = None
        self._dep_rows: list[_DepRow] = []
        self._dep_rows_container: QWidget | None = None
        self._dep_rows_layout: QVBoxLayout | None = None
        self._output_mode_combo: QComboBox | None = None
        self._td_text_console_cb: QCheckBox | None = None
        self._td_disable_update_cb: QCheckBox | None = None
        self._td_log_level_combo: QComboBox | None = None
        self._td_quick_crash_cb: QCheckBox | None = None
        self._td_disable_crash_cb: QCheckBox | None = None
        self._td_ten_bit_color_cb: QCheckBox | None = None
        self._td_double_draw_cb: QCheckBox | None = None
        self._td_editor_edit: QLineEdit | None = None
        self._td_table_editor_edit: QLineEdit | None = None
        self._td_web_launch_edit: QLineEdit | None = None
        self._td_help_delay_set_cb: QCheckBox | None = None
        self._td_help_delay_spin: QSpinBox | None = None
        self._td_midi_chan_edit: QLineEdit | None = None
        self._thumb_preview: QLabel | None = None
        self._clear_btn: QPushButton | None = None
        # Values that come from alpacka.local.yml. Their widgets are
        # read-only and save passes them through untouched, so they can
        # never be copied into alpacka.yml.
        self._owned = project.LocalOverrides()
        self._run_primary_locked = False
        self._run_args_locked = False
        self._output_locked = False

        self._build_chrome()

        # Live status refresh for the Launch Dependencies section. A
        # ~4s tick is fast enough to feel responsive without piling on
        # DB reads. The timer only runs while the page is visible
        # (start/stop in showEvent/hideEvent).
        self._dep_status_timer = QTimer(self)
        self._dep_status_timer.setInterval(4000)
        self._dep_status_timer.timeout.connect(self._refresh_running_state)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_entry(self, entry: library.LibraryEntry) -> None:
        """Load `entry` into the form. Clears any previous state."""
        self._entry = entry
        self._populate()
        # If the page is already visible, prime the status pills now so
        # the user sees current state without waiting for the first tick.
        if self.isVisible():
            self._refresh_running_state()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        # Re-prime on every show so toggling between Library and Edit
        # Details reflects current process state immediately, not after
        # the next ~4s tick.
        self._refresh_running_state()
        self._dep_status_timer.start()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        super().hideEvent(event)
        self._dep_status_timer.stop()

    # ------------------------------------------------------------------
    # Live status refresh — Launch Dependencies pills
    # ------------------------------------------------------------------

    def _refresh_running_state(self) -> None:
        """Update each dep row's status pill to reflect live process state.

        Correlates rows to records by index using
        `record.run_config_name == "dep:<i>"`. If the user re-ordered or
        added rows since launch, the indices shift and pills may show
        stale-ish state until the next launch — accepted v1 trade-off
        in exchange for not having to plumb a stable ID per row.
        """
        if self._entry is None or not self._dep_rows:
            return
        try:
            records = process_tracker.list_for_project(self._entry.row_id)
        except Exception:  # noqa: BLE001
            return

        dep_records: dict[int, "process_tracker.ProcessRecord"] = {}
        any_active = False
        for rec in records:
            if rec.kind != "dependency":
                if not rec.completed_at:
                    any_active = True
                continue
            if not rec.completed_at:
                any_active = True
            name = rec.run_config_name or ""
            if name.startswith("dep:"):
                try:
                    idx = int(name.split(":", 1)[1])
                except (IndexError, ValueError):
                    continue
                dep_records[idx] = rec

        for index, row in enumerate(self._dep_rows):
            rec = dep_records.get(index)
            text, state = _dep_status_text_and_state(rec, project_active=any_active)
            row.status_pill.setText(text)
            row.status_pill.setProperty("state", state)
            # Force a style re-polish so the new state property takes
            # effect (Qt caches styling until unpolish/polish).
            style = row.status_pill.style()
            if style is not None:
                style.unpolish(row.status_pill)
                style.polish(row.status_pill)
            row.status_pill.setVisible(bool(text))

    # ------------------------------------------------------------------
    # Construction — sticky header + scrollable content area
    # ------------------------------------------------------------------

    def _build_chrome(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header())

        self._scroll = QScrollArea()
        self._scroll.setObjectName("editDetailsScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(self._scroll, stretch=1)

        self._content_widget = QWidget()
        self._content_widget.setObjectName("editDetailsContent")
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(24, 16, 24, 24)
        self._content_layout.setSpacing(12)
        self._content_layout.addStretch(1)

        viewport = _CenteredColumnViewport(self._content_widget, _CONTENT_MAX_WIDTH)
        viewport.setObjectName("editDetailsViewport")
        self._scroll.setWidget(viewport)

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("editDetailsHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(24, 14, 24, 14)
        row.setSpacing(8)

        self._title_label = QLabel("Edit Details")
        self._title_label.setObjectName("sectionHeading")
        row.addWidget(self._title_label)

        row.addStretch(1)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(lambda _=False: self._on_cancel())
        row.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("primaryButton")
        save_btn.setDefault(True)
        save_btn.clicked.connect(lambda _=False: self._on_save())
        row.addWidget(save_btn)

        return header

    # ------------------------------------------------------------------
    # Content rebuild — runs every time `set_entry` is called
    # ------------------------------------------------------------------

    def _populate(self) -> None:
        # Wipe the existing content widgets so the page reflects the
        # currently-set entry rather than whichever project was edited
        # previously.
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        # Reset per-entry widget refs so stale ones can't be queried after
        # an unreadable-project header runs and skips re-creating them.
        self.name_edit = None
        self.description_edit = None
        self.tag_bar = None
        self._run_primary_edit = None
        self._run_args_edit = None
        self._var_rows = []
        self._var_rows_container = None
        self._var_rows_layout = None
        self._dep_rows = []
        self._dep_rows_container = None
        self._dep_rows_layout = None
        self._output_mode_combo = None
        self._td_text_console_cb = None
        self._td_disable_update_cb = None
        self._td_log_level_combo = None
        self._td_quick_crash_cb = None
        self._td_disable_crash_cb = None
        self._td_ten_bit_color_cb = None
        self._td_double_draw_cb = None
        self._td_editor_edit = None
        self._td_table_editor_edit = None
        self._td_web_launch_edit = None
        self._td_help_delay_set_cb = None
        self._td_help_delay_spin = None
        self._td_midi_chan_edit = None
        self._thumb_preview = None
        self._clear_btn = None
        self._owned = project.LocalOverrides()
        self._run_primary_locked = False
        self._run_args_locked = False
        self._output_locked = False

        if self._entry is None or self._entry.project is None:
            self._title_label.setText("Edit Details")
            unreadable = QLabel(
                "alpacka.yml is missing or unreadable. There's nothing to edit."
            )
            unreadable.setObjectName("pathPreview")
            unreadable.setWordWrap(True)
            self._content_layout.addWidget(unreadable)
            self._content_layout.addStretch(1)
            return

        proj = self._entry.project
        self._title_label.setText(f"Edit Details — {proj.name or self._entry.path.name}")
        self._owned = project.local_overrides(proj)

        notes = list(proj.load_warnings)
        if self._owned.any:
            notes.append(
                "Greyed-out values come from alpacka.local.yml on this machine. "
                "ALPACKA never copies them into alpacka.yml; change them in "
                "alpacka.local.yml."
            )
        for text in notes:
            note = QLabel(text)
            note.setObjectName("pathPreview")
            note.setWordWrap(True)
            self._content_layout.addWidget(note)

        # Editable form: name / description / tags
        editable_form = QFormLayout()
        editable_form.setSpacing(8)
        editable_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.name_edit = QLineEdit(proj.name)
        editable_form.addRow("Name:", self.name_edit)

        self.description_edit = QTextEdit(proj.description)
        self.description_edit.setFixedHeight(80)
        editable_form.addRow("Description:", self.description_edit)

        self.tag_bar = TagChipBar()
        self.tag_bar.set_tags(list(proj.tags))
        self.tag_bar.edit_requested.connect(self._on_edit_tags)
        editable_form.addRow("Tags:", self.tag_bar)

        for key, widget in (
            ("name", self.name_edit),
            ("description", self.description_edit),
            ("tags", self.tag_bar),
        ):
            if key in self._owned.top:
                _lock(widget)

        form_wrapper = QWidget()
        form_wrapper.setLayout(editable_form)
        self._content_layout.addWidget(form_wrapper)

        # Run section — entry / command / args, per kind. TD has no
        # editable launch surface (argv is fixed to the .toe path), so
        # the section is hidden there.
        if proj.kind in _RUN_FIELD_SPECS:
            self._content_layout.addWidget(self._make_section_separator())
            self._content_layout.addWidget(self._build_run_section(proj))

        self._content_layout.addWidget(self._make_section_separator())
        self._content_layout.addWidget(self._build_variables_section(proj))
        self._content_layout.addWidget(self._make_section_separator())
        self._content_layout.addWidget(self._build_deps_section(proj))
        # Output mode is a launcher concept that doesn't apply to TD
        # projects (TD owns its own window + log). Skip the section
        # entirely there — combo stays None so save() doesn't touch it.
        if proj.kind != kinds.default_kind():
            self._content_layout.addWidget(self._make_section_separator())
            self._content_layout.addWidget(self._build_output_section(proj))
        else:
            # TD-only: TOUCH_* env-var toggles + log level picker. Slots
            # into the place output mode occupies for other kinds.
            self._content_layout.addWidget(self._make_section_separator())
            self._content_layout.addWidget(self._build_td_launch_section(proj))
        self._content_layout.addWidget(self._make_section_separator())
        self._content_layout.addWidget(self._build_thumbnail_section(proj))
        self._content_layout.addWidget(self._make_section_separator())
        self._content_layout.addWidget(self._build_readonly_section(proj))

        # Trailing stretch so content sits at the top while short entries
        # don't balloon the table to fill the page.
        self._content_layout.addStretch(1)

    def _make_section_separator(self) -> QWidget:
        """A 1px horizontal divider between sections.

        Plain `QWidget` with `WA_StyledBackground` so the QSS rule on
        `#editDetailsSeparator` paints the background fill (regular
        QWidgets don't paint backgrounds without that flag). Color
        matches the env-row dividers (`#2B302F` / A_DARK_2) for
        visual consistency.
        """
        sep = QWidget()
        sep.setObjectName("editDetailsSeparator")
        sep.setFixedHeight(1)
        sep.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        return sep

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def _build_run_section(self, proj: project.Project) -> QWidget:
        """Per-kind launch-surface editor: primary field + arguments.

        Reads / writes via `_RUN_FIELD_SPECS` so all six non-TD kinds
        share a single layout shape. The primary value lives either in
        `kind_data.<kind>.<key>` (Python's `entry`, Executable's `path`)
        or on the default run_config's `command` field (NodeJS / Shell /
        Docker-compose / Generic). Args always go to the default
        run_config's `args` list, shlex-split on save.
        """
        spec = _RUN_FIELD_SPECS[proj.kind]
        handler = kinds.get(proj.kind)
        default_cfg_name = handler.default_run_config(proj).name

        # Resolve current values from disk-state.
        if "kind_data_key" in spec:
            primary_value = str(
                proj.kind_data.get(proj.kind, {}).get(spec["kind_data_key"], "")
                or ""
            )
        else:
            current_cfg = self._find_run_config(proj, default_cfg_name)
            primary_value = str((current_cfg or {}).get("command", "") or "")

        current_cfg = self._find_run_config(proj, default_cfg_name)
        args_list = (current_cfg or {}).get("args") or []
        args_value = shlex.join(str(a) for a in args_list) if args_list else ""

        wrapper = QWidget()
        box = QVBoxLayout(wrapper)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)

        heading = QLabel("Run command")
        heading.setObjectName("tertiaryHeading")
        box.addWidget(heading)

        hint = QLabel(spec["hint"])
        hint.setObjectName("pathPreview")
        hint.setWordWrap(True)
        box.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._run_primary_edit = QLineEdit(primary_value)
        self._run_primary_edit.setPlaceholderText(spec["placeholder"])
        form.addRow(spec["label"], self._run_primary_edit)

        self._run_args_edit = QLineEdit(args_value)
        self._run_args_edit.setPlaceholderText(
            "Optional command-line arguments (space-separated; quote args with spaces)"
        )
        form.addRow("Arguments:", self._run_args_edit)

        local_cfg_keys = self._owned.run_configs.get(default_cfg_name, frozenset())
        if "kind_data_key" in spec:
            self._run_primary_locked = spec["kind_data_key"] in self._owned.kind_data.get(
                proj.kind, frozenset()
            )
        else:
            self._run_primary_locked = "command" in local_cfg_keys
        self._run_args_locked = "args" in local_cfg_keys
        if self._run_primary_locked:
            _lock(self._run_primary_edit)
        if self._run_args_locked:
            _lock(self._run_args_edit)

        form_wrapper = QWidget()
        form_wrapper.setLayout(form)
        box.addWidget(form_wrapper)

        return wrapper

    @staticmethod
    def _find_run_config(
        proj: project.Project, name: str
    ) -> dict[str, Any] | None:
        for cfg in proj.run_configs:
            if isinstance(cfg, dict) and cfg.get("name") == name:
                return cfg
        return None

    def _build_variables_section(self, proj: project.Project) -> QWidget:
        wrapper = QWidget()
        box = QVBoxLayout(wrapper)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)

        heading = QLabel("Environment Variables")
        heading.setObjectName("tertiaryHeading")
        box.addWidget(heading)

        hint = QLabel(
            "Injected as env vars and substitutable as {VAR} into run "
            "commands. Edit alpacka.yml directly for advanced options "
            "(enum types, regex validation)."
        )
        hint.setObjectName("pathPreview")
        hint.setWordWrap(True)
        box.addWidget(hint)

        # A vertical stack of inline rows instead of a QTableWidget — the
        # field placeholders carry the column meaning, dividers between
        # rows give the structure, and each row gets a per-row X to
        # remove it. The container is hidden when there are zero rows.
        self._var_rows = []
        self._var_rows_container = QWidget()
        self._var_rows_layout = QVBoxLayout(self._var_rows_container)
        self._var_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._var_rows_layout.setSpacing(0)
        box.addWidget(self._var_rows_container)

        for name, value in proj.variables.items():
            self._append_var_row(
                name, value, proj.variable_meta.get(name, {}),
                locked=name in self._owned.variables,
            )
        self._update_var_section_visibility()

        controls = QHBoxLayout()
        controls.setSpacing(6)
        add_btn = QPushButton("Add variable")
        add_btn.clicked.connect(lambda _=False: self._add_var_row())
        controls.addWidget(add_btn)
        controls.addStretch(1)
        box.addLayout(controls)

        return wrapper

    def _build_deps_section(self, proj: project.Project) -> QWidget:
        """Editor for `proj.launch_dependencies` — companion processes that
        spawn alongside the main launch.

        Each row is name + command + three flag checkboxes (enabled,
        oneshot, keep alive) + a status pill (filled in live by
        `_refresh_running_state` while the project is running) + remove.
        Mirrors the env-var section's row pattern; same dynamic row
        container, same blank-row-on-save filtering.
        """
        wrapper = QWidget()
        box = QVBoxLayout(wrapper)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)

        heading = QLabel("Launch Dependencies")
        heading.setObjectName("tertiaryHeading")
        box.addWidget(heading)

        hint = QLabel(
            "Commands ALPACKA spawns alongside the main launch — sidecar "
            "scripts, dev servers, helper processes. Output follows this "
            "project's output mode. Commands support {VAR} substitution "
            "from the project's variables."
        )
        hint.setObjectName("pathPreview")
        hint.setWordWrap(True)
        box.addWidget(hint)

        self._dep_rows = []
        self._dep_rows_container = QWidget()
        self._dep_rows_layout = QVBoxLayout(self._dep_rows_container)
        self._dep_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._dep_rows_layout.setSpacing(0)
        box.addWidget(self._dep_rows_container)

        for index, dep in enumerate(proj.launch_dependencies):
            if isinstance(dep, dict):
                self._append_dep_row(
                    name=str(dep.get("name", "") or ""),
                    command=str(dep.get("command", "") or ""),
                    enabled=bool(dep.get("enabled", True)),
                    oneshot=bool(dep.get("oneshot", False)),
                    keep_alive=bool(dep.get("keep_alive", False)),
                    original=dep if index in self._owned.dependency_indexes else None,
                )
        self._update_dep_section_visibility()

        controls = QHBoxLayout()
        controls.setSpacing(6)
        add_btn = QPushButton("Add dependency")
        add_btn.clicked.connect(lambda _=False: self._add_dep_row())
        controls.addWidget(add_btn)
        controls.addStretch(1)
        box.addLayout(controls)

        return wrapper

    def _build_output_section(self, proj: project.Project) -> QWidget:
        """Picker for `Project.output_mode` — non-TD projects only.

        Empty selection ("Default for kind") round-trips as an empty
        string in alpacka.yml so projects following the kind default
        keep a minimal on-disk footprint.
        """
        wrapper = QWidget()
        box = QVBoxLayout(wrapper)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)

        heading = QLabel("Output")
        heading.setObjectName("tertiaryHeading")
        box.addWidget(heading)

        hint = QLabel(
            "Where Launch routes stdout/stderr. \"Console\" opens a real "
            "OS terminal. \"Inline\" streams output into a window inside "
            "ALPACKA. \"None\" launches silently."
        )
        hint.setObjectName("pathPreview")
        hint.setWordWrap(True)
        box.addWidget(hint)

        handler = kinds.get(proj.kind)
        kind_default = getattr(handler, "default_output_mode", "none")
        default_label = _OUTPUT_MODE_LABELS.get(kind_default, kind_default)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._output_mode_combo = QComboBox()
        # First entry is the kind-driven default — userData "" means
        # "don't write output_mode to disk; let effective_output_mode
        # resolve via the kind handler".
        self._output_mode_combo.addItem(
            f"Default for kind ({default_label})", ""
        )
        self._output_mode_combo.addItem("Console — separate terminal", "console")
        self._output_mode_combo.addItem("Inline — captured in ALPACKA", "inline")
        self._output_mode_combo.addItem("None — silent", "none")

        # Match the on-disk value (empty string snaps to the first item).
        target = proj.output_mode or ""
        idx = self._output_mode_combo.findData(target)
        if idx < 0:
            idx = 0
        self._output_mode_combo.setCurrentIndex(idx)
        form.addRow("Output mode:", self._output_mode_combo)
        self._output_locked = "output_mode" in self._owned.top
        if self._output_locked:
            _lock(self._output_mode_combo)

        form_wrapper = QWidget()
        form_wrapper.setLayout(form)
        box.addWidget(form_wrapper)

        return wrapper

    def _build_td_launch_section(self, proj: project.Project) -> QWidget:
        """Toggles for TOUCH_* env vars + app log level (TD projects only).

        Each toggle maps onto `kind_data.touchdesigner.launch_settings`;
        the TD handler translates them to env vars at launch time. The
        section is intentionally curated — power users with niche TOUCH_*
        vars or per-machine settings (GPU affinity) use `run_configs[].env`
        and `alpacka.local.yml` respectively.
        """
        wrapper = QWidget()
        box = QVBoxLayout(wrapper)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)

        heading = QLabel("TouchDesigner Launch")
        heading.setObjectName("tertiaryHeading")
        box.addWidget(heading)

        hint = QLabel(
            "These set TOUCH_* environment variables on the TD process "
            "at launch. Empty / unchecked fields fall back to whatever "
            "TouchDesigner's own preferences (or your OS default browser, "
            "for help links) define. Per-machine settings — editor paths, "
            "GPU affinity flags — are usually best kept in alpacka.local.yml "
            "so they don't follow the project into git."
        )
        hint.setObjectName("pathPreview")
        hint.setWordWrap(True)
        box.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._td_text_console_cb = QCheckBox("Open text console at launch")
        self._td_text_console_cb.setChecked(proj.td_text_console)
        form.addRow("TOUCH_TEXT_CONSOLE:", self._td_text_console_cb)

        self._td_disable_update_cb = QCheckBox("Skip update check")
        self._td_disable_update_cb.setChecked(proj.td_disable_update_check)
        form.addRow("TOUCH_NO_UPDATE_CHECK:", self._td_disable_update_cb)

        self._td_log_level_combo = QComboBox()
        for label, value in _TD_LOG_LEVELS:
            self._td_log_level_combo.addItem(label, value)
        idx = self._td_log_level_combo.findData(proj.td_app_log_level)
        if idx < 0:
            idx = 0
        self._td_log_level_combo.setCurrentIndex(idx)
        form.addRow("TOUCH_APP_LOG_LEVEL:", self._td_log_level_combo)

        self._td_quick_crash_cb = QCheckBox("Quick crash (skip the dialog)")
        self._td_quick_crash_cb.setChecked(proj.td_quick_crash)
        form.addRow("TOUCH_QUICK_CRASH:", self._td_quick_crash_cb)

        self._td_disable_crash_cb = QCheckBox("Disable crash handlers")
        self._td_disable_crash_cb.setChecked(proj.td_disable_crash_handlers)
        form.addRow("TOUCH_DISABLE_CRASH_HANDLERS:", self._td_disable_crash_cb)

        self._td_ten_bit_color_cb = QCheckBox("Enable 10-bit color")
        self._td_ten_bit_color_cb.setChecked(proj.td_ten_bit_color)
        form.addRow("TOUCH_10_BIT_COLOR:", self._td_ten_bit_color_cb)

        self._td_double_draw_cb = QCheckBox("Enable double draw (legacy)")
        self._td_double_draw_cb.setChecked(proj.td_double_draw)
        self._td_double_draw_cb.setToolTip(
            "Outputs every rendered frame twice. The TouchDesigner docs "
            "flag this as a legacy feature — prefer the Window COMP's "
            "double-draw parameter when possible."
        )
        form.addRow("TOUCH_DOUBLE_DRAW:", self._td_double_draw_cb)

        self._td_editor_edit = QLineEdit(proj.td_editor)
        self._td_editor_edit.setPlaceholderText(
            "leave empty to use TouchDesigner's preferences"
        )
        form.addRow(
            "TOUCH_EDITOR:",
            self._wrap_with_browse(
                self._td_editor_edit, "Choose external text editor"
            ),
        )

        self._td_table_editor_edit = QLineEdit(proj.td_table_editor)
        self._td_table_editor_edit.setPlaceholderText(
            "leave empty to fall back to the text editor / TD prefs"
        )
        form.addRow(
            "TOUCH_TABLE_EDITOR:",
            self._wrap_with_browse(
                self._td_table_editor_edit, "Choose external table editor"
            ),
        )

        # TOUCH_WEB_LAUNCH is a path to an alternate browser, not a flag.
        self._td_web_launch_edit = QLineEdit(proj.td_web_launch)
        self._td_web_launch_edit.setPlaceholderText(
            "leave empty to use your OS default browser"
        )
        form.addRow(
            "TOUCH_WEB_LAUNCH:",
            self._wrap_with_browse(
                self._td_web_launch_edit, "Choose alternate browser"
            ),
        )

        # TOUCH_HELP_DELAY is in seconds; 0 explicitly *disables* pop-up
        # help, so the "Set" toggle distinguishes "unset / TD default"
        # from "explicitly 0".
        help_row = QWidget()
        help_layout = QHBoxLayout(help_row)
        help_layout.setContentsMargins(0, 0, 0, 0)
        help_layout.setSpacing(8)
        self._td_help_delay_set_cb = QCheckBox("Set")
        self._td_help_delay_spin = QSpinBox()
        self._td_help_delay_spin.setRange(0, 600)
        self._td_help_delay_spin.setSuffix(" s")
        self._td_help_delay_spin.setToolTip(
            "Seconds before the parameter pop-up help appears. Set to 0 "
            "to disable pop-up help entirely."
        )
        current_help = proj.td_help_delay
        is_help_set = current_help is not None
        self._td_help_delay_set_cb.setChecked(is_help_set)
        self._td_help_delay_spin.setEnabled(is_help_set)
        self._td_help_delay_spin.setValue(current_help if is_help_set else 0)
        self._td_help_delay_set_cb.toggled.connect(
            self._td_help_delay_spin.setEnabled
        )
        help_layout.addWidget(self._td_help_delay_set_cb)
        help_layout.addWidget(self._td_help_delay_spin)
        help_layout.addStretch(1)
        form.addRow("TOUCH_HELP_DELAY:", help_row)

        # TOUCH_MIDI_CHAN_OVERRIDE is a space-separated triple, not a single channel.
        self._td_midi_chan_edit = QLineEdit(proj.td_midi_chan_override)
        self._td_midi_chan_edit.setPlaceholderText(
            'e.g. "0 2 4" — remaps input1, input2, output'
        )
        form.addRow("TOUCH_MIDI_CHAN_OVERRIDE:", self._td_midi_chan_edit)

        # Settings from alpacka.local.yml: read-only (path fields lock their
        # Browse button too, via the wrapper widget).
        by_key: dict[str, list[QWidget]] = {
            "text_console": [self._td_text_console_cb],
            "disable_update_check": [self._td_disable_update_cb],
            "app_log_level": [self._td_log_level_combo],
            "quick_crash": [self._td_quick_crash_cb],
            "disable_crash_handlers": [self._td_disable_crash_cb],
            "ten_bit_color": [self._td_ten_bit_color_cb],
            "double_draw": [self._td_double_draw_cb],
            "editor": [self._td_editor_edit.parentWidget() or self._td_editor_edit],
            "table_editor": [
                self._td_table_editor_edit.parentWidget() or self._td_table_editor_edit
            ],
            "web_launch": [self._td_web_launch_edit.parentWidget() or self._td_web_launch_edit],
            "help_delay": [self._td_help_delay_set_cb, self._td_help_delay_spin],
            "midi_chan_override": [self._td_midi_chan_edit],
        }
        for key in self._owned.launch_settings:
            for widget in by_key.get(key, []):
                _lock(widget)

        form_wrapper = QWidget()
        form_wrapper.setLayout(form)
        box.addWidget(form_wrapper)

        return wrapper

    def _build_thumbnail_section(self, proj: project.Project) -> QWidget:
        wrapper = QWidget()
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        self._thumb_preview = QLabel()
        self._thumb_preview.setObjectName("thumbPreview")
        self._thumb_preview.setFixedSize(_THUMB_PREVIEW_W, _THUMB_PREVIEW_H)
        self._thumb_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._refresh_thumbnail_preview(proj)
        row.addWidget(self._thumb_preview)

        controls = QVBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)

        heading = QLabel("Thumbnail")
        heading.setObjectName("tertiaryHeading")
        controls.addWidget(heading)

        hint = QLabel(
            "Drop a PNG/JPG at `.launcher-thumb.png` in the project folder "
            "and it'll be picked up automatically."
        )
        hint.setObjectName("pathPreview")
        hint.setWordWrap(True)
        controls.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        choose = QPushButton("Choose image...")
        choose.clicked.connect(self._on_pick_thumbnail)
        buttons.addWidget(choose)
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setEnabled(proj.has_thumbnail)
        self._clear_btn.clicked.connect(self._on_clear_thumbnail)
        buttons.addWidget(self._clear_btn)
        buttons.addStretch(1)
        controls.addLayout(buttons)
        controls.addStretch(1)

        row.addLayout(controls, stretch=1)
        return wrapper

    def _build_readonly_section(self, proj: project.Project) -> QWidget:
        wrapper = QWidget()
        box = QVBoxLayout(wrapper)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)

        heading = QLabel("Project metadata")
        heading.setObjectName("tertiaryHeading")
        box.addWidget(heading)

        form = QFormLayout()
        form.setSpacing(6)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        for label, value in self._readonly_fields(proj):
            value_label = QLabel(value or "—")
            value_label.setObjectName("pathPreview")
            value_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            form.addRow(f"{label}:", value_label)

        form_wrapper = QWidget()
        form_wrapper.setLayout(form)
        box.addWidget(form_wrapper)
        return wrapper

    def _readonly_fields(self, proj: project.Project) -> list[tuple[str, str]]:
        handler = kinds.get(proj.kind)
        rows: list[tuple[str, str]] = [
            ("Project type", handler.display_name),
            ("Template", proj.template),
        ]
        if proj.kind == "touchdesigner":
            rows.extend(
                [
                    ("TD version", proj.td_version),
                    ("TD major", proj.td_major),
                    ("TOE file", proj.toe_file),
                ]
            )
        rows.extend(
            [
                ("Created", proj.created),
                ("Folder", str(proj.folder).replace("\\", "/")),
            ]
        )
        return rows

    # ------------------------------------------------------------------
    # Variables table — row management + height autosize
    # ------------------------------------------------------------------

    def _add_var_row(self) -> None:
        self._append_var_row("", "", {})
        self._update_var_section_visibility()

    def _append_var_row(self, name: str, value, meta: dict, *, locked: bool = False) -> None:
        if self._var_rows_layout is None:
            return

        row_frame = QFrame()
        row_frame.setObjectName("varRow")
        row_frame.setFrameShape(QFrame.Shape.NoFrame)

        layout = QHBoxLayout(row_frame)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(8)

        name_edit = QLineEdit(str(name))
        name_edit.setPlaceholderText("Name")
        layout.addWidget(name_edit, stretch=2)

        value_edit = QLineEdit(str(value) if value != "" else "")
        value_edit.setPlaceholderText("Value")
        layout.addWidget(value_edit, stretch=3)

        type_combo = QComboBox()
        type_combo.addItems(_VAR_TYPES)
        inferred = meta.get("type") or _infer_var_type(value)
        idx = type_combo.findText(inferred)
        if idx >= 0:
            type_combo.setCurrentIndex(idx)
        type_combo.setFixedWidth(_VAR_TYPE_COL_WIDTH)
        layout.addWidget(type_combo)

        desc_edit = QLineEdit(str(meta.get("description", "") or ""))
        desc_edit.setPlaceholderText("Description")
        layout.addWidget(desc_edit, stretch=3)

        remove_btn = QPushButton()
        remove_btn.setObjectName("varRemoveButton")
        remove_btn.setFlat(True)
        remove_btn.setToolTip("Remove variable")
        remove_btn.setIcon(QIcon(str(_ICONS_DIR / "x.svg")))
        remove_btn.setIconSize(QSize(14, 14))
        remove_btn.setFixedSize(24, 24)
        # Capture the row frame, not its index — list positions shift on
        # removal, so a baked-in index would point at the wrong row after
        # the first deletion.
        remove_btn.clicked.connect(
            lambda _checked=False, w=row_frame: self._remove_var_row(w)
        )
        layout.addWidget(remove_btn)

        row = _VarRow(row_frame, name_edit, value_edit, type_combo, desc_edit)
        if locked:
            row.original = (value, dict(meta or {}))
            for widget in (name_edit, value_edit, type_combo, desc_edit, remove_btn):
                _lock(widget)
        self._var_rows.append(row)
        self._var_rows_layout.addWidget(row_frame)

    def _remove_var_row(self, row_widget: QFrame) -> None:
        if self._var_rows_layout is None:
            return
        for i, r in enumerate(self._var_rows):
            if r.widget is row_widget:
                self._var_rows.pop(i)
                self._var_rows_layout.removeWidget(row_widget)
                row_widget.deleteLater()
                break
        self._update_var_section_visibility()

    def _update_var_section_visibility(self) -> None:
        if self._var_rows_container is None:
            return
        self._var_rows_container.setVisible(bool(self._var_rows))

    # ------------------------------------------------------------------
    # Launch dependencies — row management + save
    # ------------------------------------------------------------------

    def _add_dep_row(self) -> None:
        self._append_dep_row("", "", True, False, False)
        self._update_dep_section_visibility()

    def _append_dep_row(
        self,
        name: str,
        command: str,
        enabled: bool,
        oneshot: bool,
        keep_alive: bool,
        *,
        original: dict | None = None,
    ) -> None:
        """Add a row. `original` marks a dependency from alpacka.local.yml:
        its widgets are read-only and save passes the dict through as is."""
        if self._dep_rows_layout is None:
            return

        row_frame = QFrame()
        row_frame.setObjectName("depRow")
        row_frame.setFrameShape(QFrame.Shape.NoFrame)

        layout = QHBoxLayout(row_frame)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(8)

        name_edit = QLineEdit(str(name))
        name_edit.setPlaceholderText("Name (optional)")
        layout.addWidget(name_edit, stretch=2)

        command_edit = QLineEdit(str(command))
        command_edit.setPlaceholderText("Command — e.g. python scripts/worker.py")
        layout.addWidget(command_edit, stretch=5)

        enabled_check = QCheckBox("Enabled")
        enabled_check.setChecked(bool(enabled))
        enabled_check.setToolTip(
            "Uncheck to leave the row configured but skip launching it."
        )
        layout.addWidget(enabled_check)

        oneshot_check = QCheckBox("Run once")
        oneshot_check.setChecked(bool(oneshot))
        oneshot_check.setToolTip(
            "The process exits on its own. ALPACKA records its exit code "
            "and won't auto-restart it with the main app."
        )
        layout.addWidget(oneshot_check)

        keep_alive_check = QCheckBox("Keep alive")
        keep_alive_check.setChecked(bool(keep_alive))
        keep_alive_check.setToolTip(
            "Leave this dependency running when the main app exits or "
            "crashes. Stop on the project card always terminates it."
        )
        layout.addWidget(keep_alive_check)

        # Status pill — populated by `_refresh_running_state` (Phase 4).
        # Hidden until there's something to show.
        status_pill = QLabel("")
        status_pill.setObjectName("depStatusPill")
        status_pill.setProperty("state", "idle")
        status_pill.setVisible(False)
        layout.addWidget(status_pill)

        remove_btn = QPushButton()
        remove_btn.setObjectName("varRemoveButton")
        remove_btn.setFlat(True)
        remove_btn.setToolTip("Remove dependency")
        remove_btn.setIcon(QIcon(str(_ICONS_DIR / "x.svg")))
        remove_btn.setIconSize(QSize(14, 14))
        remove_btn.setFixedSize(24, 24)
        remove_btn.clicked.connect(
            lambda _checked=False, w=row_frame: self._remove_dep_row(w)
        )
        layout.addWidget(remove_btn)

        row = _DepRow(
            row_frame,
            name_edit,
            command_edit,
            enabled_check,
            oneshot_check,
            keep_alive_check,
            status_pill,
        )
        if original is not None:
            row.original = original
            for widget in (
                name_edit, command_edit, enabled_check, oneshot_check,
                keep_alive_check, remove_btn,
            ):
                _lock(widget)
        self._dep_rows.append(row)
        self._dep_rows_layout.addWidget(row_frame)

    def _remove_dep_row(self, row_widget: QFrame) -> None:
        if self._dep_rows_layout is None:
            return
        for i, r in enumerate(self._dep_rows):
            if r.widget is row_widget:
                self._dep_rows.pop(i)
                self._dep_rows_layout.removeWidget(row_widget)
                row_widget.deleteLater()
                break
        self._update_dep_section_visibility()

    def _update_dep_section_visibility(self) -> None:
        if self._dep_rows_container is None:
            return
        self._dep_rows_container.setVisible(bool(self._dep_rows))

    def _read_launch_dependencies(self) -> list[dict[str, object]] | None:
        """Return the dep list, or None if a command failed to parse.

        Empty-command rows are silently dropped (matches Variables'
        blank-name behavior). On unbalanced quotes (`shlex.ValueError`)
        we surface a frameless warning and return None so `_on_save`
        can abort cleanly.
        """
        out: list[dict[str, object]] = []
        for r in self._dep_rows:
            if r.original is not None:
                out.append(r.original)  # from alpacka.local.yml, unchanged
                continue
            name = r.name_edit.text().strip()
            command = r.command_edit.text().strip()
            if not command:
                continue
            try:
                shlex.split(command, posix=True)
            except ValueError as exc:
                label = name or command
                frameless.warning(
                    self,
                    "Invalid dependency command",
                    f"Could not parse command for '{label}': {exc}",
                )
                return None
            entry: dict[str, object] = {"command": command}
            if name:
                entry["name"] = name
            entry["enabled"] = bool(r.enabled_check.isChecked())
            if r.oneshot_check.isChecked():
                entry["oneshot"] = True
            if r.keep_alive_check.isChecked():
                entry["keep_alive"] = True
            out.append(entry)
        return out

    def _read_variables(
        self,
    ) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
        variables: dict[str, object] = {}
        variable_meta: dict[str, dict[str, object]] = {}
        for r in self._var_rows:
            name = r.name_edit.text().strip()
            if not name:
                continue
            if r.original is not None:
                # From alpacka.local.yml: pass through exactly as loaded.
                variables[name], orig_meta = r.original
                if orig_meta:
                    variable_meta[name] = dict(orig_meta)
                continue
            value_text = r.value_edit.text()
            var_type = r.type_combo.currentText()
            desc = r.desc_edit.text().strip()

            variables[name] = _coerce_var_value(value_text, var_type)
            meta: dict[str, object] = {}
            if var_type != _infer_var_type(variables[name]):
                meta["type"] = var_type
            if desc:
                meta["description"] = desc
            if meta:
                variable_meta[name] = meta
        return variables, variable_meta

    # ------------------------------------------------------------------
    # Thumbnail
    # ------------------------------------------------------------------

    def _refresh_thumbnail_preview(self, proj: project.Project) -> None:
        if self._thumb_preview is None:
            return
        if proj.has_thumbnail:
            pix = QPixmap(str(proj.thumbnail_path))
            if not pix.isNull():
                pix = pix.scaled(
                    QSize(_THUMB_PREVIEW_W, _THUMB_PREVIEW_H),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._thumb_preview.setPixmap(pix)
                return
        self._thumb_preview.clear()
        self._thumb_preview.setText("No thumbnail")

    def _wrap_with_browse(
        self, line_edit: QLineEdit, dialog_title: str
    ) -> QWidget:
        """Pack a line edit with a small "Browse…" button into one form row.

        The line edit takes the available width; the button just nudges
        the picker dialog open. Used for `TOUCH_EDITOR`, `TOUCH_TABLE_EDITOR`,
        and `TOUCH_WEB_LAUNCH` since their values are filesystem paths.
        """
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(line_edit, 1)
        button = QPushButton("Browse…")
        button.clicked.connect(
            lambda _=False, le=line_edit, t=dialog_title: self._on_pick_executable(le, t)
        )
        row.addWidget(button)
        return container

    def _on_pick_executable(self, line_edit: QLineEdit, dialog_title: str) -> None:
        """Open a file picker and write the chosen path into `line_edit`."""
        current = line_edit.text().strip()
        start_dir = ""
        if current:
            existing = Path(current)
            if existing.is_file():
                start_dir = str(existing.parent)
            elif existing.is_dir():
                start_dir = str(existing)
        chosen, _filter = QFileDialog.getOpenFileName(
            self, dialog_title, start_dir
        )
        if chosen:
            line_edit.setText(chosen)

    def _on_pick_thumbnail(self) -> None:
        if self._entry is None or self._entry.project is None:
            return
        proj = self._entry.project
        start_dir = str(proj.folder)
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Choose thumbnail image", start_dir, _IMAGE_FILTER
        )
        if not chosen:
            return
        try:
            shutil.copyfile(Path(chosen), proj.thumbnail_path)
        except OSError as exc:
            frameless.critical(self, "Could not set thumbnail", str(exc))
            return
        self._refresh_thumbnail_preview(proj)
        if self._clear_btn is not None:
            self._clear_btn.setEnabled(True)

    def _on_clear_thumbnail(self) -> None:
        if self._entry is None or self._entry.project is None:
            return
        proj = self._entry.project
        try:
            proj.thumbnail_path.unlink(missing_ok=True)
        except OSError as exc:
            frameless.critical(self, "Could not clear thumbnail", str(exc))
            return
        self._refresh_thumbnail_preview(proj)
        if self._clear_btn is not None:
            self._clear_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Tag picker
    # ------------------------------------------------------------------

    def _on_edit_tags(self) -> None:
        if self.tag_bar is None:
            return
        result = TagPickerDialog.pick(self, self.tag_bar.tags())
        if result is not None:
            self.tag_bar.set_tags(result)

    # ------------------------------------------------------------------
    # Save / Cancel
    # ------------------------------------------------------------------

    def _on_save(self) -> None:
        if self._entry is None:
            self.cancelled.emit()
            return
        if self.name_edit is None:
            # Unreadable project — Save is a no-op; just navigate back.
            self.cancelled.emit()
            return

        name = self.name_edit.text().strip()
        if not name:
            frameless.warning(self, "Missing field", "Project name is required.")
            return

        description = (
            self.description_edit.toPlainText().strip()
            if self.description_edit is not None
            else ""
        )
        tags = self.tag_bar.tags() if self.tag_bar is not None else []

        # Reload from disk to preserve any changes another tool (e.g. AcroCore)
        # may have written between when the page opened and now.
        proj = project.load(self._entry.path)
        if proj is None:
            frameless.critical(
                self,
                "Could not save",
                f"alpacka.yml is missing or unreadable at {self._entry.path}",
            )
            return

        # The run settings as they are before this edit; if those were the
        # approved ones, the edited ones are approved too (approval.py).
        approved_before = approval.was_approved(self._entry.row_id, proj)

        proj.name = name
        proj.description = description
        proj.tags = tags
        variables, variable_meta = self._read_variables()
        proj.variables = variables
        proj.variable_meta = variable_meta
        # Launch dependencies — `None` return means a command failed to
        # parse and a warning has already been surfaced. Bail out so the
        # user can fix the input without losing any other field edits.
        new_deps = self._read_launch_dependencies()
        if new_deps is None:
            return
        proj.launch_dependencies = new_deps
        # output_mode is None for TD projects (section not rendered) —
        # leave the existing value alone in that case.
        if self._output_mode_combo is not None and not self._output_locked:
            proj.output_mode = str(self._output_mode_combo.currentData() or "")
        # TD-only launch settings. Setters pop falsy keys; save() prunes
        # an empty launch_settings dict so default-state projects keep a
        # minimal alpacka.yml.
        if self._td_text_console_cb is not None:
            proj.td_text_console = self._td_text_console_cb.isChecked()
            proj.td_disable_update_check = self._td_disable_update_cb.isChecked()
            proj.td_app_log_level = str(
                self._td_log_level_combo.currentData() or ""
            )
            proj.td_quick_crash = self._td_quick_crash_cb.isChecked()
            proj.td_disable_crash_handlers = self._td_disable_crash_cb.isChecked()
            proj.td_ten_bit_color = self._td_ten_bit_color_cb.isChecked()
            proj.td_double_draw = self._td_double_draw_cb.isChecked()
            proj.td_editor = self._td_editor_edit.text().strip()
            proj.td_table_editor = self._td_table_editor_edit.text().strip()
            proj.td_web_launch = self._td_web_launch_edit.text().strip()
            # help_delay: None when "Set" is off; explicit int (incl. 0) when on.
            proj.td_help_delay = (
                int(self._td_help_delay_spin.value())
                if self._td_help_delay_set_cb.isChecked()
                else None
            )
            proj.td_midi_chan_override = self._td_midi_chan_edit.text().strip()
        # Run section is None for TD (and any kind not in the spec table) —
        # leave run_configs / kind_data alone in that case.
        if self._run_primary_edit is not None and self._run_args_edit is not None:
            self._save_run_fields(proj)

        try:
            project.save(proj)
        except OSError as exc:
            frameless.critical(self, "Could not save", str(exc))
            return
        approval.carry_forward(self._entry.row_id, approved_before)

        self.saved.emit(self._entry.row_id)

    def _save_run_fields(self, proj: project.Project) -> None:
        """Persist Run-section edits into kind_data and/or run_configs.

        Primary value goes to either `kind_data.<kind>.<key>` or the
        default run_config's `command` per `_RUN_FIELD_SPECS`. Args are
        always shlex-split into the default run_config's `args`. Empty
        primary or args clears the corresponding key so the on-disk
        diff stays minimal; empty run_config dicts (only `name`) are
        pruned at the end.
        """
        spec = _RUN_FIELD_SPECS.get(proj.kind)
        if spec is None:
            return
        handler = kinds.get(proj.kind)
        default_cfg_name = handler.default_run_config(proj).name

        primary_value = self._run_primary_edit.text().strip()  # type: ignore[union-attr]
        args_text = self._run_args_edit.text().strip()  # type: ignore[union-attr]
        try:
            args_list = shlex.split(args_text) if args_text else []
        except ValueError as exc:
            # shlex raises on unbalanced quotes — surface a clear error
            # rather than silently dropping the user's input.
            frameless.warning(
                self,
                "Invalid arguments",
                f"Could not parse arguments: {exc}",
            )
            args_list = []

        # Primary field — kind_data path or run_config command path. A field
        # from alpacka.local.yml is read-only, so it's left alone.
        if self._run_primary_locked:
            pass
        elif "kind_data_key" in spec:
            kind_section = proj.kind_data.setdefault(proj.kind, {})
            if primary_value:
                kind_section[spec["kind_data_key"]] = primary_value
            else:
                kind_section.pop(spec["kind_data_key"], None)
        else:
            cfg = _ensure_default_run_config(proj, default_cfg_name)
            if primary_value:
                cfg["command"] = primary_value
            else:
                cfg.pop("command", None)

        # Args — always on the default run_config.
        if not self._run_args_locked:
            cfg = _ensure_default_run_config(proj, default_cfg_name)
            if args_list:
                cfg["args"] = args_list
            else:
                cfg.pop("args", None)

        _prune_empty_run_configs(proj)

    def _on_cancel(self) -> None:
        self.cancelled.emit()


def _dep_status_text_and_state(
    record: "process_tracker.ProcessRecord | None",
    *,
    project_active: bool,
) -> tuple[str, str]:
    """Map a dep's tracking record to a status pill (text, state) tuple.

    `state` drives the QSS color via `[state="..."]` selectors:
      - `running`  — process is alive and well
      - `done`     — one-shot exited cleanly (exit 0)
      - `error`    — exited with a non-zero code
      - `stopped`  — persistent dep died unexpectedly (briefly visible
        before sweep_dead removes the row)
      - `idle`     — project running, dep not yet spawned (or row was
        added since launch and isn't tracked)
    Empty text ⇒ pill hidden (no project activity ⇒ no status to show).
    """
    if record is None:
        if project_active:
            return ("– not spawned", "idle")
        return ("", "idle")
    if record.completed_at:
        # mark_completed has fired — exit_code is authoritative.
        code = record.exit_code if record.exit_code is not None else 0
        if code == 0:
            return ("✓ done", "done")
        return (f"✗ exit {process_tracker.format_exit_code(code)}", "error")
    return (f"● running {record.pid}", "running")


def _infer_var_type(value) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "string"


def _coerce_var_value(text: str, var_type: str):
    if var_type == "int":
        try:
            return int(text)
        except ValueError:
            return text
    if var_type == "float":
        try:
            return float(text)
        except ValueError:
            return text
    if var_type == "bool":
        return text.strip().lower() in ("true", "1", "yes", "y", "on")
    return text


class _VarRow:
    """Bundle of widgets for a single env-var row.

    Held by `EditDetailsView._var_rows` so save/remove can address the
    fields by reference instead of by row index — indices shift as
    other rows are removed.
    """

    __slots__ = ("widget", "name_edit", "value_edit", "type_combo", "desc_edit", "original")

    def __init__(
        self,
        widget: QFrame,
        name_edit: QLineEdit,
        value_edit: QLineEdit,
        type_combo: QComboBox,
        desc_edit: QLineEdit,
    ) -> None:
        self.widget = widget
        self.name_edit = name_edit
        self.value_edit = value_edit
        self.type_combo = type_combo
        self.desc_edit = desc_edit
        # (value, meta) when the variable comes from alpacka.local.yml.
        self.original: tuple[object, dict] | None = None


class _DepRow:
    """Bundle of widgets for a single launch-dependency row.

    The status pill is created up-front but hidden by default; the live
    refresh path (Phase 4) toggles its visibility + text + state property
    while the project is running.
    """

    __slots__ = (
        "widget",
        "name_edit",
        "command_edit",
        "enabled_check",
        "oneshot_check",
        "keep_alive_check",
        "status_pill",
        "original",
    )

    def __init__(
        self,
        widget: QFrame,
        name_edit: QLineEdit,
        command_edit: QLineEdit,
        enabled_check: QCheckBox,
        oneshot_check: QCheckBox,
        keep_alive_check: QCheckBox,
        status_pill: QLabel,
    ) -> None:
        self.widget = widget
        self.name_edit = name_edit
        self.command_edit = command_edit
        self.enabled_check = enabled_check
        self.oneshot_check = oneshot_check
        self.keep_alive_check = keep_alive_check
        self.status_pill = status_pill
        # The dependency dict when it comes from alpacka.local.yml.
        self.original: dict | None = None
