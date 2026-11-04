"""Fetch + launch a TouchDesigner installer.

Derivative publishes installers at URLs like:

    https://download.derivative.ca/TouchDesigner.2025.32460.exe
    https://download.derivative.ca/TouchDesigner.2025.32460.dmg

Silent install isn't supported — we download the file, check it's signed
by Derivative (Windows), launch the installer UI and wait for it, then let
the caller re-detect installs.
"""
from __future__ import annotations

import os
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import paths, td_install

_BASE_URL = "https://download.derivative.ca"
_TIMEOUT = 30  # seconds without data before a download gives up


class DownloadError(RuntimeError):
    pass


@dataclass
class DownloadProgress:
    downloaded: int
    total: int  # 0 if unknown


ProgressCallback = Callable[[DownloadProgress], None]


def _check_version(version: str) -> str:
    if not td_install.is_valid_version(version):
        raise DownloadError(
            f"'{version}' isn't a TouchDesigner version number (like 2025.32460)."
        )
    return version


def installer_url(version: str) -> str:
    ext = _installer_extension()
    return f"{_BASE_URL}/TouchDesigner.{_check_version(version)}.{ext}"


def installer_cache_path(version: str) -> Path:
    ext = _installer_extension()
    cache = paths.cache_root() / "installers"
    cache.mkdir(parents=True, exist_ok=True)
    return cache / f"TouchDesigner.{_check_version(version)}.{ext}"


def download(
    version: str,
    *,
    on_progress: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_status: Callable[[str], None] | None = None,
) -> Path:
    """Download the installer for `version` and return its local path.

    A cached installer is reused only if it still passes `verify_installer`;
    a fresh download that fails it is deleted and refused.
    """
    def status(text: str) -> None:
        if on_status:
            on_status(text)

    dest = installer_cache_path(version)
    if dest.is_file():
        status("Checking the installer is signed by Derivative...")
        if verify_installer(dest):
            if on_progress:
                size = dest.stat().st_size
                on_progress(DownloadProgress(downloaded=size, total=size))
            return dest
        dest.unlink(missing_ok=True)

    url = installer_url(version)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:  # noqa: S310
            if not str(resp.geturl()).startswith("https://"):
                raise DownloadError(f"Refusing a download redirected to {resp.geturl()}")
            total = int(resp.headers.get("Content-Length", "0") or 0)
            downloaded = 0
            chunk_size = 1024 * 256  # 256 KB
            with tmp.open("wb") as f:
                while True:
                    if should_cancel and should_cancel():
                        raise DownloadError("Cancelled")
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if on_progress:
                        on_progress(DownloadProgress(downloaded=downloaded, total=total))
    except DownloadError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise DownloadError(f"Download failed: {exc}") from exc

    tmp.replace(dest)
    status("Checking the installer is signed by Derivative...")
    if not verify_installer(dest):
        dest.unlink(missing_ok=True)
        raise DownloadError(
            "The downloaded installer isn't signed by Derivative, so ALPACKA "
            "won't run it. Download TouchDesigner from derivative.ca instead."
        )
    return dest


# Derivative signs its Windows installers (and TouchDesigner itself) with a
# certificate whose subject includes this organisation.
_DERIVATIVE_SIGNER = "O=Derivative Inc."

_SIGNATURE_SCRIPT = (
    "$s = Get-AuthenticodeSignature -LiteralPath $env:ALPACKA_INSTALLER; "
    '"$($s.Status)|$($s.SignerCertificate.Subject)"'
)


def verify_installer(path: Path) -> bool:
    """True if `path` is a Derivative-signed installer ALPACKA may run.

    Windows: the file's Authenticode signature must be Valid and issued to
    Derivative Inc. (checked with PowerShell's Get-AuthenticodeSignature;
    the path goes in an environment variable so nothing is quoted into a
    command line). macOS: the .dmg is only opened for the user to drag the
    app across, and Gatekeeper checks the app when it's first run.
    """
    if sys.platform != "win32":
        return True
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SIGNATURE_SCRIPT],
            env={**os.environ, "ALPACKA_INSTALLER": str(path)},
            capture_output=True,
            text=True,
            timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    status, _, subject = (out.stdout or "").strip().partition("|")
    return status == "Valid" and _DERIVATIVE_SIGNER in subject


class InstallerProcess:
    """A running installer; `wait()` blocks until it exits."""

    def __init__(self, handle: int | None) -> None:
        self._handle = handle

    def wait(self) -> None:
        if not self._handle:
            return
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.WaitForSingleObject(ctypes.c_void_p(self._handle), 0xFFFFFFFF)
        kernel32.CloseHandle(ctypes.c_void_p(self._handle))
        self._handle = None


def run_installer(installer_path: Path) -> InstallerProcess | None:
    """Launch the installer UI. Returns a waitable handle (Windows) or None (macOS).

    Windows uses ShellExecuteEx, the same call Explorer uses, so the
    installer can still ask for elevation (UAC) and no command line is
    built (the old `cmd /c start` re-parsed `&`, `|` … in the path).
    """
    installer_path = Path(installer_path)
    if sys.platform == "win32":
        return InstallerProcess(_shell_execute(installer_path))
    if sys.platform == "darwin":
        # Open the .dmg — the user must drag the .app into /Applications.
        subprocess.Popen(["open", str(installer_path)])
        return None
    raise DownloadError(f"Unsupported platform: {sys.platform}")


def _shell_execute(path: Path, parameters: str | None = None, show: int = 1) -> int | None:
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "open"
    info.lpFile = str(path)
    info.lpParameters = parameters
    info.lpDirectory = str(Path(path).parent) if Path(path).is_absolute() else None
    info.nShow = show
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        raise DownloadError(
            f"Couldn't start the installer: {ctypes.WinError(ctypes.get_last_error())}"
        )
    return info.hProcess or None


def _installer_extension() -> str:
    if sys.platform == "win32":
        return "exe"
    if sys.platform == "darwin":
        return "dmg"
    raise DownloadError(f"Unsupported platform: {sys.platform}")
