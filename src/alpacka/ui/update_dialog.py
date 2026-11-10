"""Install an ALPACKA update from inside the app (the Windows installer build).

`run_update(parent, info)` warns about inline projects that will stop,
downloads and checks the setup with a progress dialog, then starts it and
quits. The setup waits for ALPACKA to exit, installs over it and opens it
again (see `updater` and `installer/alpacka.iss`). Anything that goes
wrong before ALPACKA quits leaves it unchanged and offers the release
page instead.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel, QProgressBar, QWidget

from .. import library, output_manager, updater
from ..downloads import DownloadError, human_bytes
from . import frameless
from .frameless import FramelessDialog


def run_update(parent: QWidget | None, info: updater.UpdateInfo) -> None:
    """Download, check and start the update, then quit ALPACKA."""
    names = _inline_project_names()
    if names:
        listed = "\n".join(f"  • {name}" for name in names)
        if not frameless.question(
            parent,
            "Update ALPACKA",
            "Updating closes and reopens ALPACKA. These projects show their output "
            f"in ALPACKA, so they'll stop:\n\n{listed}\n\n"
            "Projects set to start automatically start again when ALPACKA reopens.",
            yes="Update and restart",
            no="Cancel",
        ):
            return

    dialog = UpdateProgressDialog(info, parent)
    dialog.start()
    if dialog.installer is None:
        if dialog.error and dialog.error != "Cancelled":
            _offer_release_page(parent, info, dialog.error)
        return

    log = updater.install_log_path()
    updater.record_pending(info, log)
    try:
        updater.start_installer(
            updater.installer_command(dialog.installer, wait_pid=os.getpid(), log_path=log)
        )
    except DownloadError as exc:
        updater.clear_pending()
        _offer_release_page(parent, info, str(exc))
        return

    # A normal quit, so settings are saved and inline children stopped
    # cleanly. The setup is waiting for this process to exit.
    app = QApplication.instance()
    if app is not None:
        app.closeAllWindows()
        app.quit()


def _inline_project_names() -> list[str]:
    names: list[str] = []
    for row_id in output_manager.running_inline_rows():
        entry = library.get(row_id)
        if entry is None:
            continue
        names.append((entry.project.name if entry.project else "") or entry.path.name)
    return names


def _offer_release_page(parent: QWidget | None, info: updater.UpdateInfo, message: str) -> None:
    if frameless.question(
        parent,
        "Couldn't update ALPACKA",
        f"{message}\n\nALPACKA hasn't changed. Open the release page to download "
        "the installer yourself?",
        yes="Open Release Page",
        no="Close",
        default_yes=True,
    ):
        QDesktopServices.openUrl(QUrl(updater.safe_release_url(info.release_page)))


class _DownloadWorker(QObject):
    progress = Signal(int, int)  # downloaded, total
    finished = Signal(object)  # Path
    failed = Signal(str)

    def __init__(self, info: updater.UpdateInfo) -> None:
        super().__init__()
        self._info = info
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            path = updater.download_installer(
                self._info,
                on_progress=lambda p: self.progress.emit(p.downloaded, p.total),
                should_cancel=lambda: self._cancel,
            )
        except DownloadError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — a full disk, say; never crash the UI
            self.failed.emit(f"Download failed: {exc}")
            return
        self.finished.emit(path)


class UpdateProgressDialog(FramelessDialog):
    """Downloads and checks the setup. `installer` is set on success."""

    def __init__(self, info: updater.UpdateInfo, parent: QWidget | None = None) -> None:
        super().__init__(f"Update to ALPACKA {info.version}", parent, resizable=False)
        self.resize(460, 0)
        self.installer: Path | None = None
        self.error = ""

        layout = self.content_layout
        layout.setSpacing(10)

        self.status_label = QLabel(f"Downloading ALPACKA {info.version}...")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        layout.addWidget(self.progress_bar)

        note = QLabel("ALPACKA closes, installs the update and opens again when this finishes.")
        note.setObjectName("templateDescription")
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self._on_cancel)
        layout.addWidget(buttons)

        self._thread = QThread(self)
        self._worker = _DownloadWorker(info)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)

    def start(self) -> int:
        self._thread.start()
        return self.exec()

    def reject(self) -> None:  # the title bar's close button lands here too
        self._worker.cancel()
        self._stop_thread()
        super().reject()

    def _on_progress(self, downloaded: int, total: int) -> None:
        if total > 0:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(int(downloaded * 100 / total))
            self.status_label.setText(
                f"Downloading... {human_bytes(downloaded)} / {human_bytes(total)}"
            )
        else:
            self.progress_bar.setRange(0, 0)
            self.status_label.setText(f"Downloading... {human_bytes(downloaded)}")

    def _on_finished(self, path: object) -> None:
        self._stop_thread()
        self.installer = Path(str(path))
        self.accept()

    def _on_failed(self, message: str) -> None:
        self.error = message
        self.reject()

    def _on_cancel(self) -> None:
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("Cancelling...")
        self._worker.cancel()

    def _stop_thread(self) -> None:
        if not self._thread.isRunning():
            return
        self._thread.quit()
        if not self._thread.wait(3000):
            # Still blocked in a network read (up to its 30 s timeout). Let
            # it finish on its own instead of destroying a running thread
            # along with this dialog.
            self._thread.setParent(None)
            _finishing.append((self._thread, self._worker))


# Download threads that outlived their dialog after a cancel.
_finishing: list[tuple[QThread, _DownloadWorker]] = []
