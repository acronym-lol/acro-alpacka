"""Review a project's run settings before ALPACKA runs them (approval.py)."""
from __future__ import annotations

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QWidget

from .. import approval, project
from .frameless import FramelessDialog


class RunApprovalDialog(FramelessDialog):
    """Shows everything the project would run; Approve and Run or Cancel."""

    def __init__(self, proj: project.Project, parent: QWidget | None = None) -> None:
        super().__init__("Review Before Running", parent)
        self.resize(640, 0)
        layout = self.content_layout

        intro = QLabel(
            "This project's settings ask ALPACKA to run the following on this "
            "machine. Approve it only if you trust where the project came from. "
            "ALPACKA asks again if any of it changes."
        )
        intro.setObjectName("dialogMessage")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        details = QPlainTextEdit(approval.describe(proj))
        details.setObjectName("runApprovalDetails")
        details.setReadOnly(True)
        details.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        details.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        details.setMinimumHeight(220)
        layout.addWidget(details, stretch=1)

        note = QLabel(
            "Approving covers these settings from alpacka.yml / alpacka.local.yml. "
            "The project's own code (its .toe, package scripts) still runs as usual."
        )
        note.setObjectName("templateDescription")
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        run = QPushButton("Approve and Run")
        run.setObjectName("primaryButton")
        run.clicked.connect(self.accept)
        buttons.addWidget(run)
        layout.addLayout(buttons)
        # Cancel is the default: Enter must not approve by accident.
        cancel.setDefault(True)
        cancel.setFocus()

    def showEvent(self, event) -> None:  # type: ignore[override]
        # A top-level window takes its minimum height from the layout's
        # minimum, which ignores what the wrapped labels need at the actual
        # width, so they overlap the text box. Grow to fit.
        super().showEvent(event)
        layout = self.layout()
        if layout is not None and layout.hasHeightForWidth():
            needed = layout.totalHeightForWidth(self.width())
            if needed > self.height():
                self.resize(self.width(), needed)
