"""Modeless live-tail dialog for a project's captured output.

Shown by clicking the "View output" icon on a project card whose
effective output_mode is ``"inline"``. Subscribes to the project's
:class:`OutputBuffer` so the body updates as new lines arrive.

Lifecycle:
  - One dialog per project (``MainWindow`` keeps a ``dict[row_id, OutputView]``).
  - Modeless — multiple projects can show their logs side by side.
  - On open, snapshot+connect under the buffer's lock so every line is
    accounted for exactly once (no missed line in the gap between
    snapshot and signal connect, no replayed line after).
  - Close button just hides; reopening the same project's dialog
    reuses the existing instance via MainWindow's dict.

Toolbar:
  - **Clear** — drops all lines from the buffer, in-place.
  - **Copy all** — copies the entire visible body to the clipboard.
  - **Save to file…** — writes the current body to a user-chosen path.
  - **Auto-scroll** (toggle) — when on, new lines pin the scroll to the
    bottom; off, the user's scroll position is preserved while new
    lines arrive (useful for inspecting earlier output during a chatty
    run).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import output_manager
from . import frameless


class OutputView(frameless.FramelessDialog):
    """Live tail for a single project's output buffer."""

    def __init__(
        self,
        project_name: str,
        row_id: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(f"Output — {project_name}", parent=parent)
        # FramelessDialog defaults to modal; flip it back so multiple
        # OutputViews can stay open at once and the main window stays
        # responsive while logs scroll.
        self.setModal(False)
        self.resize(880, 520)

        self._row_id = row_id
        self._buffer = output_manager.get_buffer(row_id)

        self._build_body()
        self._rehydrate_and_connect()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_body(self) -> None:
        # Status row: pill on the left, toolbar on the right.
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)

        self._status_pill = QLabel("Idle")
        self._status_pill.setObjectName("outputStatusPill")
        self._status_pill.setProperty("status", "idle")
        status_row.addWidget(self._status_pill)
        status_row.addStretch(1)

        self._auto_scroll = QCheckBox("Auto-scroll")
        self._auto_scroll.setChecked(True)
        status_row.addWidget(self._auto_scroll)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda _=False: self._on_clear())
        status_row.addWidget(clear_btn)

        copy_btn = QPushButton("Copy all")
        copy_btn.clicked.connect(lambda _=False: self._on_copy_all())
        status_row.addWidget(copy_btn)

        save_btn = QPushButton("Save to file…")
        save_btn.clicked.connect(lambda _=False: self._on_save_to_file())
        status_row.addWidget(save_btn)

        self.content_layout.addLayout(status_row)

        # Body: monospace, read-only, dark.
        self._text = QPlainTextEdit()
        self._text.setObjectName("outputBody")
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._text.setMaximumBlockCount(0)  # we cap upstream in OutputBuffer
        self._configure_monospace_font()
        self.content_layout.addWidget(self._text, stretch=1)

        # Empty-state hint shown only when the buffer has zero lines.
        self._empty_hint = QLabel(
            "No output captured yet — Launch the project to see stdout/stderr."
        )
        self._empty_hint.setObjectName("pathPreview")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content_layout.addWidget(self._empty_hint)

    def _configure_monospace_font(self) -> None:
        font = QFont()
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFamilies(["Consolas", "Menlo", "DejaVu Sans Mono", "Courier New"])
        font.setPointSize(11)
        self._text.setFont(font)

    # ------------------------------------------------------------------
    # Buffer wiring
    # ------------------------------------------------------------------

    def _rehydrate_and_connect(self) -> None:
        """Snapshot the buffer + connect to signals atomically.

        OutputBuffer.snapshot() takes the same lock that append_lines
        uses, so any line added after our snapshot fires through the
        signal we're about to connect — no gap, no double-count.
        """
        lines, status, exit_code = self._buffer.snapshot()
        if lines:
            self._text.appendPlainText("\n".join(lines))
            self._text.moveCursor(QTextCursor.MoveOperation.End)
        self._update_status_pill(status, exit_code)
        self._update_empty_state(bool(lines))

        # Connect AFTER snapshot to avoid replaying lines we already pulled.
        self._buffer.lines_appended.connect(self._on_lines_appended)
        self._buffer.status_changed.connect(self._update_status_pill)

    def _on_lines_appended(self, batch: list) -> None:
        if not batch:
            # Empty batch is the buffer's "you should reload" nudge —
            # fired on clear. Wipe and re-snapshot to stay in sync.
            self._text.clear()
            lines, status, exit_code = self._buffer.snapshot()
            if lines:
                self._text.appendPlainText("\n".join(lines))
            self._update_status_pill(status, exit_code)
            self._update_empty_state(bool(lines))
            return

        # Append in one chunk, not per-line, to amortize layout cost.
        self._text.appendPlainText("\n".join(str(s) for s in batch))
        if self._auto_scroll.isChecked():
            self._text.moveCursor(QTextCursor.MoveOperation.End)
        self._update_empty_state(True)

    def _update_status_pill(self, status: str, exit_code: int) -> None:
        if status == "running":
            self._status_pill.setText("Running")
        elif status == "exited":
            self._status_pill.setText(f"Exited ({exit_code})")
        else:
            self._status_pill.setText("Idle")
        self._status_pill.setProperty("status", status or "idle")
        # Force QSS attribute-selector restyle.
        self._status_pill.style().unpolish(self._status_pill)
        self._status_pill.style().polish(self._status_pill)

    def _update_empty_state(self, has_content: bool) -> None:
        self._empty_hint.setVisible(not has_content)

    # ------------------------------------------------------------------
    # Toolbar handlers
    # ------------------------------------------------------------------

    def _on_clear(self) -> None:
        self._buffer.clear()
        self._text.clear()
        self._update_empty_state(False)

    def _on_copy_all(self) -> None:
        text = self._text.toPlainText()
        if not text:
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)

    def _on_save_to_file(self) -> None:
        text = self._text.toPlainText()
        if not text:
            return
        default_name = f"output-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Save output", default_name, "Log files (*.log *.txt);;All files (*)"
        )
        if not path_str:
            return
        try:
            Path(path_str).write_text(text, encoding="utf-8", newline="\n")
        except OSError as exc:
            frameless.critical(self, "Could not save output", str(exc))


__all__ = ["OutputView"]
