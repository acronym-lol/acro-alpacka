"""New Project view — full-page editor for creating a new project.

Mounted in the main window's content stack (alongside Library / Templates /
Settings / Edit Details) instead of opening as a modal dialog. The page
has a sticky header with Create / Cancel; the form lives below in a
vertical QScrollArea so templates with a lot of variables / scaffold
inputs (SvelteKit's 10 add-ons, for example) don't overflow the viewport.

Lifecycle:
  - ``reset(app_settings)`` is called each time the view is entered so
    previous state doesn't linger.
  - ``created(CreateResult)`` fires after `create.create_project` returns
    successfully. The MainWindow refreshes the library, optionally fires
    a launch, then switches the stack back to the library.
  - ``cancelled()`` fires when the user backs out.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
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

from .. import create, kinds, settings, td_install, templates
from . import frameless
from .edit_details_view import _CenteredColumnViewport
from .tag_picker_dialog import TagChipBar, TagPickerDialog


_INSTALL_SUBCOMMANDS = {"install", "i", "ci", "add"}
_PM_TOOLS = {"npm", "pnpm", "yarn", "bun"}

# Maximum reading-column width for the form area. Matches Edit Details.
# Wider windows pad the sides; narrower windows let the column shrink.
_CONTENT_MAX_WIDTH = 880

# Display labels for output_mode values — used by the output-mode
# picker's "Default for kind (...)" line.
_OUTPUT_MODE_LABELS = {
    "console": "Console",
    "inline": "Inline",
    "none": "None",
}


def _scaffold_labels(argv: list[str], tmpl) -> tuple[str, str]:
    """Pick a title + heading for the scaffold progress dialog.

    Package-manager install invocations (`npm install`, `pnpm i`, etc.)
    get labelled as "Installing dependencies"; everything else reads as
    "Scaffolding <template>" so the scaffold vs post-install phase is
    obvious.
    """
    head = argv[0] if argv else ""
    subcmd = argv[1] if len(argv) > 1 else ""
    is_install = head in _PM_TOOLS and subcmd in _INSTALL_SUBCOMMANDS
    if is_install:
        return (
            "Installing dependencies",
            f"Running `{head} {subcmd}`...",
        )
    tmpl_name = tmpl.name if tmpl is not None else "template"
    return (
        f"Scaffolding {tmpl_name}",
        f"Running `{head}`...",
    )


class NewProjectView(QWidget):
    """In-stack page for creating a new project."""

    created = Signal(object)  # create.CreateResult
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("newProjectView")
        self._installs: list[settings.TDInstall] = []
        self._app_settings: settings.Settings | None = None
        # Variable name → (decl, widget, source) map for submit-time value
        # collection. Rebuilt every time the template combo changes.
        self._variable_inputs: dict[str, tuple[object, QWidget, str]] = {}

        self._build_chrome()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset(self, app_settings: settings.Settings) -> None:
        """Reinitialize the form for a fresh run."""
        self._app_settings = app_settings
        self.name_edit.clear()
        # Pre-fill the parent folder from settings if the user set one;
        # they can still browse to override per-project. Empty string =
        # "no default" (existing behavior).
        self.folder_edit.setText(app_settings.default_parent_folder or "")
        self.description_edit.clear()
        self.tag_bar.set_tags([])
        self.folders_check.setChecked(True)
        self.readme_check.setChecked(True)
        self.launch_check.setChecked(True)
        self.git_check.setChecked(True)
        self.git_remote_edit.clear()
        # Default kind → touchdesigner (if available).
        td_idx = self.kind_combo.findData(kinds.default_kind())
        if td_idx >= 0:
            self.kind_combo.setCurrentIndex(td_idx)
        self._load_installs(app_settings)
        self._on_kind_changed()  # repopulates templates + variables
        self._update_preview()
        self._update_git_subfields()
        # Focus the first field so typing goes somewhere sensible.
        self.name_edit.setFocus()

    # ------------------------------------------------------------------
    # Construction — sticky header + scrollable body
    # ------------------------------------------------------------------

    def _build_chrome(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header())

        self._scroll = QScrollArea()
        self._scroll.setObjectName("newProjectScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(self._scroll, stretch=1)

        body = QWidget()
        body.setObjectName("newProjectContent")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 16, 24, 24)
        body_layout.setSpacing(12)

        viewport = _CenteredColumnViewport(body, _CONTENT_MAX_WIDTH)
        viewport.setObjectName("newProjectViewport")
        self._scroll.setWidget(viewport)

        self._build_form(body_layout)
        self._build_variables_section(body_layout)
        self._build_output_section(body_layout)
        self._build_options_section(body_layout)
        self._build_post_create_section(body_layout)
        body_layout.addStretch(1)

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("newProjectHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(24, 14, 24, 14)
        row.setSpacing(8)

        self._title_label = QLabel("New Project")
        self._title_label.setObjectName("sectionHeading")
        row.addWidget(self._title_label)

        row.addStretch(1)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(lambda _=False: self._on_cancel())
        row.addWidget(cancel_btn)

        self.create_btn = QPushButton("Create")
        self.create_btn.setObjectName("primaryButton")
        self.create_btn.setDefault(True)
        self.create_btn.clicked.connect(lambda _=False: self._on_create())
        row.addWidget(self.create_btn)

        return header

    def _build_form(self, layout: QVBoxLayout) -> None:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("MyProject")
        self.name_edit.textChanged.connect(self._update_preview)
        form.addRow("Name:", self.name_edit)

        folder_row = QWidget()
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.setSpacing(6)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Choose parent folder...")
        self.folder_edit.textChanged.connect(self._update_preview)
        folder_layout.addWidget(self.folder_edit, stretch=1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse_folder)
        folder_layout.addWidget(browse)
        form.addRow("Parent folder:", folder_row)

        self.preview_label = QLabel()
        self.preview_label.setObjectName("pathPreview")
        self.preview_label.setWordWrap(True)
        form.addRow("", self.preview_label)

        self.kind_combo = QComboBox()
        self.kind_combo.setMinimumWidth(280)
        for handler in kinds.all_handlers():
            self.kind_combo.addItem(handler.display_name, userData=handler.name)
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        form.addRow("Project type:", self.kind_combo)

        self.template_combo = QComboBox()
        self.template_combo.setMinimumWidth(280)
        form.addRow("Template:", self.template_combo)

        self.template_desc = QLabel()
        self.template_desc.setObjectName("templateDescription")
        self.template_desc.setWordWrap(True)
        self.template_combo.currentIndexChanged.connect(self._update_template_desc)
        self.template_combo.currentIndexChanged.connect(self._rebuild_variables)
        form.addRow("", self.template_desc)

        self.version_label = QLabel("TD version:")
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(280)
        form.addRow(self.version_label, self.version_combo)

        self.description_edit = QTextEdit()
        self.description_edit.setPlaceholderText("What is this project about?")
        self.description_edit.setFixedHeight(60)
        form.addRow("Description:", self.description_edit)

        self.tag_bar = TagChipBar()
        self.tag_bar.edit_requested.connect(self._on_edit_tags)
        form.addRow("Tags:", self.tag_bar)

        wrapper = QWidget()
        wrapper.setLayout(form)
        layout.addWidget(wrapper)

    def _build_variables_section(self, layout: QVBoxLayout) -> None:
        # Rebuilt every time the template changes (see _rebuild_variables).
        # Kept hidden when the selected template declares no variables.
        self.variables_heading = QLabel("Variables")
        self.variables_heading.setObjectName("tertiaryHeading")
        self.variables_heading.setVisible(False)
        layout.addWidget(self.variables_heading)

        self.variables_container = QWidget()
        self._variables_layout = QVBoxLayout(self.variables_container)
        self._variables_layout.setContentsMargins(0, 0, 0, 0)
        self._variables_layout.setSpacing(6)
        self.variables_container.setVisible(False)
        layout.addWidget(self.variables_container)

    def _build_output_section(self, layout: QVBoxLayout) -> None:
        """Output-mode picker — hidden for TD projects.

        The first combo entry is "Default for kind (...)" with userData=""
        meaning "don't write output_mode to alpacka.yml". The displayed
        kind label is refreshed on kind change so the user always sees
        what the default would resolve to right now.
        """
        self.output_heading = QLabel("Output")
        self.output_heading.setObjectName("tertiaryHeading")
        layout.addWidget(self.output_heading)

        hint = QLabel(
            "Where Launch routes stdout/stderr. \"Console\" opens a real "
            "OS terminal. \"Inline\" streams output into a window inside "
            "ALPACKA. \"None\" launches silently."
        )
        hint.setObjectName("pathPreview")
        hint.setWordWrap(True)
        self.output_hint = hint
        layout.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.output_mode_combo = QComboBox()
        self.output_mode_combo.setMinimumWidth(280)
        # First item is the kind-default sentinel; userData "" means
        # "leave output_mode unset on disk".
        self.output_mode_combo.addItem("Default for kind", "")
        self.output_mode_combo.addItem("Console — separate terminal", "console")
        self.output_mode_combo.addItem("Inline — captured in ALPACKA", "inline")
        self.output_mode_combo.addItem("None — silent", "none")
        form.addRow("Output mode:", self.output_mode_combo)

        wrapper = QWidget()
        wrapper.setLayout(form)
        self.output_form_wrapper = wrapper
        layout.addWidget(wrapper)

    def _refresh_output_default_label(self) -> None:
        """Rewrite the first combo entry to show the current kind's default.

        Called from `_on_kind_changed` so a user who picks "Default for
        kind" sees what mode they'd actually get for the selected kind.
        Also toggles the whole section's visibility (hidden for TD).
        """
        kind_name = self.kind_combo.currentData() or kinds.default_kind()
        is_td = kind_name == kinds.default_kind()
        self.output_heading.setVisible(not is_td)
        self.output_hint.setVisible(not is_td)
        self.output_form_wrapper.setVisible(not is_td)
        if is_td:
            return
        handler = kinds.get(kind_name)
        kind_default = getattr(handler, "default_output_mode", "none")
        label = _OUTPUT_MODE_LABELS.get(kind_default, kind_default)
        self.output_mode_combo.setItemText(0, f"Default for kind ({label})")

    def _build_options_section(self, layout: QVBoxLayout) -> None:
        options_heading = QLabel("Options")
        options_heading.setObjectName("tertiaryHeading")
        layout.addWidget(options_heading)

        box = QVBoxLayout()
        # Looser stack between the toggles so each one reads as a
        # discrete choice rather than a dense block.
        box.setSpacing(10)

        self.folders_check = QCheckBox(
            "Create basic folder structure (scripts/, content/, tox/, TDLogs/)"
        )
        self.folders_check.setChecked(True)
        box.addWidget(self.folders_check)

        self.readme_check = QCheckBox("Create README.md")
        self.readme_check.setChecked(True)
        box.addWidget(self.readme_check)

        # Visible when the selected kind produces install steps
        # (Python: venv + pip install; NodeJS: pm install). Toggled
        # via `_refresh_install_check` on kind change.
        self.install_check = QCheckBox("Install dependencies after create")
        self.install_check.setChecked(True)
        self.install_check.setVisible(False)
        # Whether the selected template is trusted to run its install
        # scripts by default (built-in or none); see _refresh_install_check.
        self._install_trusted = True
        box.addWidget(self.install_check)

        self.git_check = QCheckBox("Initialize git repository")
        self.git_check.setChecked(True)
        self.git_check.toggled.connect(self._update_git_subfields)
        box.addWidget(self.git_check)

        self.git_remote_row = QWidget()
        remote_layout = QHBoxLayout(self.git_remote_row)
        remote_layout.setContentsMargins(24, 0, 0, 0)
        remote_layout.setSpacing(6)
        remote_label = QLabel("Remote URL:")
        remote_label.setObjectName("pathPreview")
        remote_layout.addWidget(remote_label)
        self.git_remote_edit = QLineEdit()
        self.git_remote_edit.setPlaceholderText(
            "https://github.com/you/repo.git (optional)"
        )
        remote_layout.addWidget(self.git_remote_edit, stretch=1)
        box.addWidget(self.git_remote_row)

        wrapper = QWidget()
        wrapper.setLayout(box)
        layout.addWidget(wrapper)

    def _build_post_create_section(self, layout: QVBoxLayout) -> None:
        """The "after we click Create" beat — distinct from Options.

        Pulled out of the main Options group so it reads as the final
        step ("…and then launch") rather than another setup toggle.
        Label updates with the selected kind via `_on_kind_changed`.
        """
        heading = QLabel("After create")
        heading.setObjectName("tertiaryHeading")
        layout.addWidget(heading)

        box = QVBoxLayout()
        box.setSpacing(10)

        self.launch_check = QCheckBox("Launch TouchDesigner after create")
        self.launch_check.setChecked(True)
        box.addWidget(self.launch_check)

        wrapper = QWidget()
        wrapper.setLayout(box)
        layout.addWidget(wrapper)

    # ------------------------------------------------------------------
    # Population / reactions
    # ------------------------------------------------------------------

    def _load_installs(self, app_settings: settings.Settings) -> None:
        # Prefer live detection; fall back to cached settings if scan returns empty.
        detected = td_install.detect()
        self._installs = detected or list(app_settings.td_installs)

        self.version_combo.clear()
        if not self._installs:
            self.version_combo.addItem(
                "(no TD installation detected)", userData=None
            )
            # Only disable Create for TD kind; non-TD kinds don't need TD.
            if (self.kind_combo.currentData() or kinds.default_kind()) == kinds.default_kind():
                self.create_btn.setEnabled(False)
            return

        self.create_btn.setEnabled(True)
        default = app_settings.default_td_install
        default_index = 0
        for i, inst in enumerate(self._installs):
            self.version_combo.addItem(f"TouchDesigner {inst.version}", userData=inst)
            if inst.version == default:
                default_index = i
        self.version_combo.setCurrentIndex(default_index)

    def _load_templates(self) -> None:
        self.template_combo.clear()
        self.template_combo.addItem("None / minimal", userData=None)
        current_kind = self.kind_combo.currentData() or kinds.default_kind()
        for tmpl in templates.list_all(kind=current_kind):
            label = tmpl.name
            if tmpl.builtin:
                label += " (built-in)"
            if not tmpl.source_exists:
                label += "  ⚠ source missing"
            self.template_combo.addItem(label, userData=tmpl)
        if self.template_combo.count() > 1:
            self.template_combo.setCurrentIndex(1)
        self._update_template_desc()

    def _on_kind_changed(self) -> None:
        kind_name = self.kind_combo.currentData() or kinds.default_kind()
        handler = kinds.get(kind_name)

        self._load_templates()
        self._refresh_output_default_label()
        self._refresh_install_check()

        show_version = (
            handler.supports_versions and kind_name == kinds.default_kind()
        )
        self.version_label.setVisible(show_version)
        self.version_combo.setVisible(show_version)

        folder_list = ", ".join(handler.universal_folders)
        if folder_list:
            self.folders_check.setText(
                f"Create basic folder structure ({folder_list})"
            )
            self.folders_check.setVisible(True)
        else:
            self.folders_check.setVisible(False)

        self.launch_check.setText(f"Launch {handler.display_name} after create")

        # Re-enable Create if switching away from TD with no install.
        if self._app_settings is not None and not self._installs:
            if kind_name == kinds.default_kind():
                self.create_btn.setEnabled(False)
            else:
                self.create_btn.setEnabled(True)

    def _rebuild_variables(self) -> None:
        while self._variables_layout.count():
            item = self._variables_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._variable_inputs = {}

        tmpl = self.template_combo.currentData()
        scaffold_inputs, variable_decls = self._template_inputs(tmpl)

        # The install checkbox is keyed off the selected kind, not the
        # template — `_refresh_install_check` is called from
        # `_on_kind_changed` so this keeps in sync as the user picks
        # different kinds. The line here is defensive in case the
        # template change races ahead of a kind-change event.
        self._refresh_install_check()

        if not scaffold_inputs and not variable_decls:
            self.variables_heading.setVisible(False)
            self.variables_container.setVisible(False)
            return

        if scaffold_inputs and variable_decls:
            self.variables_heading.setText("Template options")
        elif scaffold_inputs:
            self.variables_heading.setText("Scaffold options")
        else:
            self.variables_heading.setText("Variables")

        if scaffold_inputs:
            if variable_decls:
                sub = QLabel("Scaffold options")
                sub.setObjectName("pathPreview")
                self._variables_layout.addWidget(sub)
            self._variables_layout.addWidget(
                self._build_variable_form(scaffold_inputs, source="scaffold")
            )
        if variable_decls:
            if scaffold_inputs:
                sub = QLabel("Runtime variables (persisted in alpacka.yml)")
                sub.setObjectName("pathPreview")
                self._variables_layout.addWidget(sub)
            self._variables_layout.addWidget(
                self._build_variable_form(variable_decls, source="runtime")
            )

        self.variables_heading.setVisible(True)
        self.variables_container.setVisible(True)

    def _build_variable_form(self, decls: list, *, source: str) -> QWidget:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)
        for decl in decls:
            label_text = decl.prompt or decl.name
            widget = self._make_variable_input(decl)
            form.addRow(f"{label_text}:", widget)
            if decl.description:
                hint = QLabel(decl.description)
                hint.setObjectName("pathPreview")
                hint.setWordWrap(True)
                form.addRow("", hint)
            self._variable_inputs[decl.name] = (decl, widget, source)
        wrapper = QWidget()
        wrapper.setLayout(form)
        return wrapper

    def _refresh_install_check(self) -> None:
        """Show the install checkbox iff the selected kind produces install steps.

        Driven by `KindHandler.produces_install_steps` (a class
        attribute) so we don't have to materialize a project folder
        before deciding visibility. The checkbox label includes the
        kind so the user knows what's about to run.
        """
        kind_name = self.kind_combo.currentData() or kinds.default_kind()
        handler = kinds.get(kind_name)
        if getattr(handler, "produces_install_steps", False):
            self.install_check.setText(
                f"Install dependencies after create ({handler.display_name})"
            )
            self.install_check.setVisible(True)
            # Installing runs the template's own package scripts (pip /
            # npm). Built-in templates are known; for anyone else's
            # template that's opt-in.
            tmpl = self.template_combo.currentData()
            trusted = tmpl is None or bool(getattr(tmpl, "builtin", False))
            if trusted != self._install_trusted:
                self._install_trusted = trusted
                self.install_check.setChecked(trusted)
            self.install_check.setToolTip(
                "" if trusted else
                "Runs this template's install scripts (pip / npm). Only tick "
                "it if you trust where the template came from."
            )
        else:
            self.install_check.setVisible(False)

    def _template_inputs(self, tmpl) -> tuple[list, list]:
        if tmpl is None or not tmpl.source_exists:
            return [], []
        try:
            from .. import template_apply
            config = template_apply.resolve_config(
                Path(tmpl.toe_path), trusted=bool(getattr(tmpl, "builtin", False))
            )
        except Exception:  # noqa: BLE001
            return [], []
        if config is None:
            return [], []
        scaffold_inputs = list(config.scaffold.inputs) if config.scaffold else []
        return scaffold_inputs, list(config.variables)

    def _make_variable_input(self, decl) -> QWidget:
        var_type = (decl.type or "string").lower()

        if var_type == "enum":
            combo = QComboBox()
            combo.setMinimumWidth(220)
            for opt in decl.options:
                combo.addItem(opt["label"], userData=opt["value"])
            default = decl.default
            if default is not None:
                idx = combo.findData(str(default))
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            return combo

        if var_type == "multiselect":
            panel = QWidget()
            box = QVBoxLayout(panel)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(2)
            defaults: set[str] = set()
            raw_default = decl.default
            if isinstance(raw_default, (list, tuple)):
                defaults = {str(v) for v in raw_default}
            elif isinstance(raw_default, str) and raw_default:
                defaults = {
                    v.strip() for v in raw_default.split(",") if v.strip()
                }
            for opt in decl.options:
                cb = QCheckBox(opt["label"])
                cb.setProperty("optionValue", opt["value"])
                cb.setChecked(opt["value"] in defaults)
                box.addWidget(cb)
            return panel

        if var_type == "bool":
            cb = QCheckBox()
            cb.setChecked(bool(decl.default))
            return cb

        if var_type == "int":
            spin = QSpinBox()
            spin.setRange(-1_000_000, 1_000_000)
            try:
                spin.setValue(int(decl.default or 0))
            except (TypeError, ValueError):
                spin.setValue(0)
            return spin

        if var_type == "float":
            dspin = QDoubleSpinBox()
            dspin.setRange(-1e9, 1e9)
            dspin.setDecimals(4)
            try:
                dspin.setValue(float(decl.default or 0))
            except (TypeError, ValueError):
                dspin.setValue(0.0)
            return dspin

        edit = QLineEdit()
        edit.setText("" if decl.default is None else str(decl.default))
        return edit

    def _read_variable_values(self) -> tuple[dict[str, object], dict[str, object]]:
        scaffold_values: dict[str, object] = {}
        runtime_values: dict[str, object] = {}
        for name, (decl, widget, source) in self._variable_inputs.items():
            var_type = (decl.type or "string").lower()
            if var_type == "enum" and isinstance(widget, QComboBox):
                value = widget.currentData() or widget.currentText()
            elif var_type == "multiselect":
                picked: list[str] = []
                for cb in widget.findChildren(QCheckBox):
                    if cb.isChecked():
                        picked.append(str(cb.property("optionValue")))
                value = picked
            elif var_type == "bool" and isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif var_type == "int" and isinstance(widget, QSpinBox):
                value = widget.value()
            elif var_type == "float" and isinstance(widget, QDoubleSpinBox):
                value = widget.value()
            elif isinstance(widget, QLineEdit):
                value = widget.text()
            else:
                value = decl.default
            if source == "scaffold":
                scaffold_values[name] = value
            else:
                runtime_values[name] = value
        return scaffold_values, runtime_values

    def _make_scaffold_runner(self):
        def runner(argv, cwd):
            from .scaffold_progress_dialog import ScaffoldProgressDialog
            tmpl = self.template_combo.currentData()
            argv_list = list(argv)
            title, heading = _scaffold_labels(argv_list, tmpl)
            # Scaffold dialog parents off the top-level window so it
            # centers properly regardless of where the view sits.
            dlg = ScaffoldProgressDialog(
                title=title,
                heading=heading,
                argv=argv_list,
                cwd=Path(cwd),
                parent=self.window(),
            )
            dlg.exec()
            if dlg.was_cancelled:
                return None
            return dlg.exit_code
        return runner

    def _update_template_desc(self) -> None:
        tmpl = self.template_combo.currentData()
        if tmpl is None:
            self.template_desc.setText("")
            return
        pieces = [tmpl.description]
        if not tmpl.source_exists:
            pieces.append(
                f"[Source missing on disk: {tmpl.toe_path or '(no path)'}]"
            )
        else:
            pieces.append(f"Source: {tmpl.toe_path}")
        self.template_desc.setText("\n".join(p for p in pieces if p))

    def _browse_folder(self) -> None:
        current = self.folder_edit.text().strip()
        start = current if current and Path(current).is_dir() else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose parent folder", start
        )
        if chosen:
            self.folder_edit.setText(chosen)

    def _update_preview(self) -> None:
        name = self.name_edit.text().strip() or "MyProject"
        parent = self.folder_edit.text().strip()
        if not parent:
            self.preview_label.setText("Pick a parent folder to see the full path.")
            return
        kind_name = self.kind_combo.currentData() or kinds.default_kind()
        if kind_name == kinds.default_kind():
            full = Path(parent) / name / f"{name}.toe"
            self.preview_label.setText(f"Will create: {full}")
        else:
            full = Path(parent) / name
            self.preview_label.setText(f"Will create: {full}/alpacka.yml")

    def _on_edit_tags(self) -> None:
        result = TagPickerDialog.pick(self, self.tag_bar.tags())
        if result is not None:
            self.tag_bar.set_tags(result)

    def _update_git_subfields(self) -> None:
        enabled = self.git_check.isChecked()
        self.git_remote_row.setEnabled(enabled)
        self.git_remote_edit.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Create / Cancel
    # ------------------------------------------------------------------

    def _on_create(self) -> None:
        name = self.name_edit.text().strip()
        parent = self.folder_edit.text().strip()
        tmpl = self.template_combo.currentData()
        kind_name = self.kind_combo.currentData() or kinds.default_kind()

        if not name:
            frameless.warning(self, "Missing field", "Project name is required.")
            return
        if not parent:
            frameless.warning(self, "Missing field", "Parent folder is required.")
            return
        if kind_name == kinds.default_kind():
            if tmpl is None:
                frameless.warning(
                    self,
                    "Missing field",
                    "A TouchDesigner project needs a template to start from.",
                )
                return
            install = self.version_combo.currentData()
            if install is None:
                frameless.warning(
                    self,
                    "No TD installed",
                    "TouchDesigner is required to launch projects. Install TD first.",
                )
                return
            td_version = install.version
            td_major = install.major
        else:
            td_version = ""
            td_major = ""

        tags = self.tag_bar.tags()

        scaffold_values, runtime_values = self._read_variable_values()

        opts = create.CreateOptions(
            name=name,
            parent_folder=Path(parent),
            template_name=tmpl.name if tmpl is not None else "",
            kind=kind_name,
            td_version=td_version,
            td_major=td_major,
            description=self.description_edit.toPlainText().strip(),
            tags=tags,
            variables=runtime_values,
            scaffold_values=scaffold_values,
            scaffold_runner=self._make_scaffold_runner(),
            install_dependencies=(
                self.install_check.isVisible()
                and self.install_check.isChecked()
            ),
            create_folders=self.folders_check.isChecked(),
            create_readme=self.readme_check.isChecked(),
            init_git=self.git_check.isChecked(),
            git_remote_url=(
                self.git_remote_edit.text().strip()
                if self.git_check.isChecked()
                else ""
            ),
            launch_after_create=self.launch_check.isChecked(),
            # TD section is hidden — read combo unconditionally; for TD
            # the empty default is harmless (TD goes through silent
            # regardless of the persisted value).
            output_mode=str(self.output_mode_combo.currentData() or ""),
        )

        try:
            result = create.create_project(opts)
        except create.CreateError as exc:
            frameless.critical(self, "Could not create project", str(exc))
            return

        self.created.emit(result)

    def _on_cancel(self) -> None:
        self.cancelled.emit()
