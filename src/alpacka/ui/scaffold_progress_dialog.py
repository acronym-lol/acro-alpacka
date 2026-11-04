"""Progress dialog for external-CLI scaffold commands.

Wraps `template_scaffold.run_subprocess` on a worker thread and surfaces
its live output in a frameless dialog with:

  - A heading line ("Scaffolding SvelteKit...") + subtle spinner
  - A collapsible "Show output" panel streaming stdout/stderr
  - A Cancel button that trips the cancel flag and terminates the
    subprocess, rolling back to a clean state on the caller's side

Reused for the optional `npm install` step that follows a successful
scaffold (see commit 8).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import template_scaffold
from .frameless import FramelessDialog


class _ScaffoldWorker(QThread):
    """Runs the subprocess on a background thread.

    Emits `output(line)` for each captured line and `completed(exit_code)`
    when the subprocess exits (or is cancelled, in which case exit_code
    is usually negative per POSIX signal convention).
    """

    output = Signal(str)
    completed = Signal(int)

    def __init__(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._argv = list(argv)
        self._cwd = Path(cwd)
        self._env = env or {}
        self._cancel = template_scaffold.CancelFlag()

    def cancel(self) -> None:
        self._cancel.cancelled = True

    def run(self) -> None:  # noqa: D401  (QThread override)
        exit_code = template_scaffold.run_subprocess(
            self._argv,
            cwd=self._cwd,
            env=self._env,
            on_output=lambda line: self.output.emit(line),
            cancel=self._cancel,
        )
        self.completed.emit(exit_code)


class ScaffoldProgressDialog(FramelessDialog):
    """Show live output from a scaffold subprocess; support cancel.

    Usage:
        dlg = ScaffoldProgressDialog(
            title="Scaffolding SvelteKit",
            heading="Running `npx sv create`...",
            argv=["npx", "--yes", "sv@latest", "create", ...],
            cwd=project_folder,
            parent=window,
        )
        dlg.exec()
        # dlg.exit_code is 0 on success, non-zero on failure, None on cancel
    """

    def __init__(
        self,
        *,
        title: str,
        heading: str,
        argv: list[str],
        cwd: Path,
        env: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, parent)
        self.resize(560, 0)
        self._exit_code: int | None = None
        self._cancelled = False
        self._build_ui(heading)

        self._worker = _ScaffoldWorker(argv, cwd, env, parent=self)
        self._worker.output.connect(self._on_output_line)
        self._worker.completed.connect(self._on_completed)

        # Start after the dialog has a chance to render its first frame so
        # output streams into an already-visible panel instead of queueing
        # behind the layout pass.
        QTimer.singleShot(50, self._worker.start)

        # Lightweight spinner animation via a char cycle on a 100ms timer.
        self._spinner_idx = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(100)
        self._spinner_timer.timeout.connect(self._tick_spinner)
        self._spinner_timer.start()

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    @property
    def was_cancelled(self) -> bool:
        return self._cancelled

    def succeeded(self) -> bool:
        return self._exit_code == 0 and not self._cancelled

    # ------------------------------------------------------------------

    def _build_ui(self, heading: str) -> None:
        layout = self.content_layout

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)
        self._spinner_label = QLabel("⠋")
        self._spinner_label.setObjectName("scaffoldSpinner")
        self._spinner_label.setFixedWidth(18)
        status_row.addWidget(self._spinner_label)
        self._heading_label = QLabel(heading)
        self._heading_label.setObjectName("scaffoldHeading")
        self._heading_label.setWordWrap(True)
        status_row.addWidget(self._heading_label, stretch=1)
        layout.addLayout(status_row)

        # Collapsible output panel — starts collapsed.
        self._toggle_btn = QPushButton("▸ Show output")
        self._toggle_btn.setObjectName("linkButton")
        self._toggle_btn.setFlat(True)
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.clicked.connect(lambda _=False: self._toggle_output())
        layout.addWidget(self._toggle_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        self._output_view = QPlainTextEdit()
        self._output_view.setObjectName("scaffoldOutput")
        self._output_view.setReadOnly(True)
        self._output_view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._output_view.setMinimumHeight(180)
        self._output_view.setVisible(False)
        layout.addWidget(self._output_view, stretch=1)

        button_row = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._cancel_btn = button_row.button(QDialogButtonBox.StandardButton.Cancel)
        self._close_btn: QPushButton | None = None
        button_row.rejected.connect(self._on_cancel_clicked)
        layout.addWidget(button_row)
        self._button_row = button_row

    def _toggle_output(self) -> None:
        visible = not self._output_view.isVisible()
        self._output_view.setVisible(visible)
        self._toggle_btn.setText("▾ Hide output" if visible else "▸ Show output")
        if visible:
            # Grow the dialog a bit so the output panel has room.
            self.resize(self.width(), max(self.height(), 360))

    def _tick_spinner(self) -> None:
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self._spinner_idx = (self._spinner_idx + 1) % len(frames)
        self._spinner_label.setText(frames[self._spinner_idx])

    # ------------------------------------------------------------------
    # Worker callbacks
    # ------------------------------------------------------------------

    def _on_output_line(self, line: str) -> None:
        self._output_view.appendPlainText(line)

    def _on_completed(self, exit_code: int) -> None:
        self._exit_code = exit_code
        self._spinner_timer.stop()
        if self._cancelled:
            self._heading_label.setText("Cancelled.")
            self._spinner_label.setText("✕")
        elif exit_code == 0:
            self._heading_label.setText("Done.")
            self._spinner_label.setText("✓")
        else:
            # Failure — auto-expand the output panel so the user can see
            # what broke without needing to click Show output.
            self._heading_label.setText(f"Failed (exit {exit_code}).")
            self._spinner_label.setText("⚠")
            if not self._output_view.isVisible():
                self._toggle_output()

        # Swap Cancel for Close so the user can dismiss the dialog.
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setVisible(False)
        close_btn = self._button_row.addButton(
            "Close", QDialogButtonBox.ButtonRole.AcceptRole
        )
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        self._close_btn = close_btn

        # Auto-close on clean success so the user isn't stuck clicking Close
        # on every create. Kept open on failure / cancel so they can read
        # the output.
        if exit_code == 0 and not self._cancelled:
            QTimer.singleShot(400, self.accept)

    def _on_cancel_clicked(self) -> None:
        if self._exit_code is not None:
            # Already finished; Cancel is effectively Close.
            self.reject()
            return
        self._cancelled = True
        self._heading_label.setText("Cancelling...")
        self._worker.cancel()
        self._cancel_btn.setEnabled(False)

    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # type: ignore[override]
        # If the user closes via the title-bar X while the process is
        # still running, treat it as a cancel.
        if self._exit_code is None:
            self._cancelled = True
            self._worker.cancel()
            # Don't block the close — the worker will terminate in the
            # background; the parent's error handling picks up the
            # cancelled flag.
        super().closeEvent(event)
