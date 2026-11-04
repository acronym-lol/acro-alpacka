"""Dialogs for handling missing-TD-install situations.

- `VersionMismatchDialog`: shown when a project pins a TD version that
  isn't installed. Offers Launch-with-newer (if a newer build is present)
  and Install-{pinned}.
- `InstallProgressDialog`: progress bar during installer download + the
  Run Installer step.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import td_download, td_install
from ..settings import TDInstall
from . import frameless
from .frameless import FramelessDialog


class MismatchChoice(Enum):
    CANCELLED = auto()
    LAUNCH_WITH_NEWER = auto()
    INSTALL_PINNED = auto()


@dataclass
class MismatchResult:
    choice: MismatchChoice
    install: TDInstall | None = None


class VersionMismatchDialog(FramelessDialog):
    def __init__(
        self,
        *,
        pinned: str,
        closest_newer: TDInstall | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("TD version not installed", parent)
        self.resize(440, 0)

        self._pinned = pinned
        self._closest_newer = closest_newer
        self._result = MismatchResult(choice=MismatchChoice.CANCELLED)

        layout = self.content_layout

        title = QLabel(f"Project requires TouchDesigner {pinned}")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        if closest_newer:
            body = QLabel(
                f"{pinned} isn't installed, but {closest_newer.version} is. "
                "You can launch with the newer build, or install the exact pinned version."
            )
        else:
            body = QLabel(
                f"{pinned} isn't installed, and no newer build is available on this machine. "
                "Install it to launch this project."
            )
        body.setWordWrap(True)
        layout.addWidget(body)

        button_row = QHBoxLayout()
        button_row.addStretch(1)

        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        button_row.addWidget(cancel)

        install_btn = QPushButton(f"Install {pinned}")
        install_btn.clicked.connect(self._on_install)
        button_row.addWidget(install_btn)

        if closest_newer:
            launch_btn = QPushButton(f"Launch with {closest_newer.version}")
            launch_btn.setObjectName("launchButton")
            launch_btn.setDefault(True)
            launch_btn.clicked.connect(self._on_launch_newer)
            button_row.addWidget(launch_btn)

        layout.addLayout(button_row)

    @property
    def result_choice(self) -> MismatchResult:
        return self._result

    def _on_launch_newer(self) -> None:
        self._result = MismatchResult(
            choice=MismatchChoice.LAUNCH_WITH_NEWER, install=self._closest_newer
        )
        self.accept()

    def _on_install(self) -> None:
        self._result = MismatchResult(choice=MismatchChoice.INSTALL_PINNED)
        self.accept()


class _DownloadWorker(QObject):
    progress = Signal(int, int)  # downloaded, total
    status = Signal(str)
    finished = Signal(object)  # Path on success
    failed = Signal(str)

    def __init__(self, version: str) -> None:
        super().__init__()
        self._version = version
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            path = td_download.download(
                self._version,
                on_progress=lambda p: self.progress.emit(p.downloaded, p.total),
                should_cancel=lambda: self._cancel,
                on_status=self.status.emit,
            )
        except td_download.DownloadError as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(path)


class InstallProgressDialog(FramelessDialog):
    """Download + run installer. Returns True on success (detected new install)."""

    def __init__(self, version: str, parent: QWidget | None = None) -> None:
        super().__init__(f"Install TouchDesigner {version}", parent, resizable=False)
        self.resize(460, 0)
        self._version = version
        self._installed_ok = False

        layout = self.content_layout
        layout.setSpacing(10)

        self.status_label = QLabel(f"Downloading TouchDesigner {version}...")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        self.detail_label = QLabel(td_download.installer_url(version))
        self.detail_label.setObjectName("pathPreview")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self._on_cancel)
        layout.addWidget(buttons)

        self._thread = QThread(self)
        self._worker = _DownloadWorker(version)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.status.connect(self._on_status)
        self._worker.finished.connect(self._on_download_finished)
        self._worker.failed.connect(self._on_download_failed)

    @property
    def succeeded(self) -> bool:
        return self._installed_ok

    def start(self) -> int:
        self._thread.start()
        return self.exec()

    # --- Slots ---

    def _on_progress(self, downloaded: int, total: int) -> None:
        if total > 0:
            pct = int(downloaded * 100 / total)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(pct)
            self.status_label.setText(
                f"Downloading... {_human_bytes(downloaded)} / {_human_bytes(total)}"
            )
        else:
            self.progress_bar.setRange(0, 0)  # indeterminate
            self.status_label.setText(
                f"Downloading... {_human_bytes(downloaded)}"
            )

    def _on_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.progress_bar.setRange(0, 0)  # indeterminate

    def _on_download_finished(self, installer_path: object) -> None:
        installer_path = Path(str(installer_path))
        self._stop_thread()

        self.status_label.setText("Launching installer...")
        self.progress_bar.setRange(0, 0)
        self.cancel_btn.setEnabled(False)

        try:
            proc = td_download.run_installer(installer_path)
        except td_download.DownloadError as exc:
            frameless.critical(self, "Install failed", str(exc))
            self.reject()
            return

        # On Windows we wait for the installer to exit; on Mac we can't block
        # on the .dmg drag-and-drop, so we bounce to the user with instructions.
        if proc is not None:
            proc.wait()

        # Re-detect installs. If the requested version now shows up, declare success.
        detected = td_install.detect()
        if any(i.version == self._version for i in detected):
            self._installed_ok = True
            self.accept()
            return

        # Not detected — either user cancelled the installer or dragged into
        # a non-standard location on Mac.
        retry = frameless.question(
            self,
            "Install complete?",
            f"TouchDesigner {self._version} was not detected after the installer closed. "
            "If you haven't finished the installation (e.g. drag-to-Applications on macOS), "
            "click 'Try again' once it's done.",
            yes="Try again",
            no="Cancel",
            default_yes=True,
        )
        if retry:
            detected = td_install.detect()
            if any(i.version == self._version for i in detected):
                self._installed_ok = True
                self.accept()
                return
            frameless.warning(
                self,
                "Still not detected",
                "The install is still not detected. You can add it manually from Settings.",
            )
        self.reject()

    def _on_download_failed(self, message: str) -> None:
        self._stop_thread()
        if message != "Cancelled":
            frameless.critical(self, "Download failed", message)
        self.reject()

    def _on_cancel(self) -> None:
        self._worker.cancel()

    def _stop_thread(self) -> None:
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
