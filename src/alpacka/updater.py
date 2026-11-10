"""Check GitHub Releases for a newer ALPACKA, and install it on Windows.

Checking: queries ``https://api.github.com/repos/acronym-lol/acro-alpacka/releases/latest``,
compares the tag against :data:`alpacka.__version__`, and surfaces an
:class:`UpdateInfo` if a newer release exists.

Installing (the Windows installer build only, see :func:`can_install_in_place`):
download that release's setup, check it against the release's
``SHA256SUMS-windows.txt``, start it silently, and quit. The setup waits
for ALPACKA to exit, installs over it and opens it again. The portable
zip, macOS and runs from source open the release page instead.

Design notes:
  * Uses ``urllib.request`` (through :mod:`alpacka.downloads`) so the
    app stays PySide6-only + stdlib. No new dependencies.
  * The network call runs on the Qt thread pool via :func:`start_check`.
    Callers receive the result on the thread that owned the signal
    connection — typically the main thread, so UI handlers are safe.
  * Check failures collapse to ``None`` (treated as "no update
    available"). The check is best-effort by design; a flaky connection
    must not interrupt launch.
  * Download URLs are built from the pinned repo and a validated tag,
    never taken from the API response.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from . import __version__, downloads, fsutil, paths
from .downloads import DownloadError, ProgressCallback

_REPO = "acronym-lol/acro-alpacka"
_API_URL = f"https://api.github.com/repos/{_REPO}/releases/latest"
_USER_AGENT = f"ALPACKA/{__version__} (+https://github.com/acronym-lol/acro-alpacka)"
_DEFAULT_TIMEOUT = 5.0


@dataclass
class UpdateInfo:
    """Info about a release newer than the currently-running version."""

    version: str        # "0.2.0" — leading ``v`` stripped
    tag: str            # "v0.2.0" — the GitHub tag name as returned
    published_at: str   # ISO-8601 from the API
    release_page: str   # HTML URL — what we open in the browser
    body: str           # release notes markdown


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------


def _parse_version(s: str) -> tuple[tuple[int, ...], str]:
    """Split ``s`` into an int-tuple base and a pre-release suffix.

    ``"v0.2.0-rc1"`` → ``((0, 2, 0), "rc1")``.
    ``"0.2.0"`` → ``((0, 2, 0), "")``.
    """
    s = s.strip().lstrip("vV")
    base, _, suffix = s.partition("-")
    try:
        parts = tuple(int(p) for p in base.split("."))
    except ValueError:
        # Malformed — treat as 0.0.0 so it compares as older than anything real.
        parts = (0,)
    return parts, suffix


def compare_versions(a: str, b: str) -> int:
    """Return -1 if ``a < b``, 0 if equal, 1 if ``a > b``.

    Tolerant of a leading ``v`` and of pre-release suffixes. A pre-release
    sorts *before* the same base version without a suffix (semver-style),
    so ``0.2.0-rc1 < 0.2.0``.
    """
    ap, asfx = _parse_version(a)
    bp, bsfx = _parse_version(b)

    # Pad the shorter tuple with zeros so 0.2 and 0.2.0 compare equal.
    length = max(len(ap), len(bp))
    ap = ap + (0,) * (length - len(ap))
    bp = bp + (0,) * (length - len(bp))
    if ap != bp:
        return -1 if ap < bp else 1

    if asfx == bsfx:
        return 0
    if not asfx:
        return 1   # a is full release, b has a pre-release suffix
    if not bsfx:
        return -1
    return -1 if asfx < bsfx else 1


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------


def check_latest(timeout: float = _DEFAULT_TIMEOUT) -> UpdateInfo | None:
    """Hit the GitHub Releases API and return an :class:`UpdateInfo` or ``None``.

    Returns ``None`` when the network fails, the response is malformed,
    or the latest tag is not strictly newer than :data:`__version__`.
    """
    req = urllib.request.Request(_API_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": _USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None

    tag = str(payload.get("tag_name") or "").strip()
    html_url = str(payload.get("html_url") or "").strip()
    published_at = str(payload.get("published_at") or "").strip()
    body = str(payload.get("body") or "")
    if not tag or not html_url:
        return None

    version = tag.lstrip("vV")
    if compare_versions(version, __version__) <= 0:
        return None

    return UpdateInfo(
        version=version,
        tag=tag,
        published_at=published_at,
        release_page=safe_release_url(html_url),
        body=body,
    )


RELEASES_URL = f"https://github.com/{_REPO}/releases"


def safe_release_url(url: str) -> str:
    """The release page to open in the browser — only ever our own.

    The URL comes from a network response; anything that isn't a page
    under this repo's GitHub releases is replaced with the releases list.
    """
    url = str(url or "").strip()
    if url.startswith(RELEASES_URL + "/") and not any(c in url for c in ' "<>\\'):
        return url
    return RELEASES_URL


# ---------------------------------------------------------------------------
# Installing an update (the Windows installer build)
# ---------------------------------------------------------------------------

# Inno Setup records the install under this key. `installer/alpacka.iss`
# declares `AppId={{928D…98}}`: Inno reads `{{` as an escaped `{` but has
# no escape for `}`, so the stored ID really ends in `}}`. Every install so
# far has used that ID; never "fix" it, or new installers stop finding
# existing installs. `tests/test_updater.py` checks the two stay in step.
INSTALLER_APP_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    r"\{928D3EA7-C282-4066-995E-A6B0CC034E98}}_is1"
)

CHECKSUMS_NAME = "SHA256SUMS-windows.txt"
_DOWNLOAD_TIMEOUT = 30  # seconds without data before a download gives up
_TAG_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")

_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def _installed_location() -> Path | None:
    """The folder the Windows installer recorded for this user, if any."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INSTALLER_APP_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "InstallLocation")
    except OSError:
        return None
    value = str(value or "").strip()
    return Path(value) if value else None


def _same_folder(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def can_install_in_place() -> bool:
    """True when this is the installed Windows build, so it can update itself.

    The running exe must be in the folder the installer recorded for this
    user. Portable copies, macOS, runs from source and a copy that lives
    anywhere else get the release page instead.
    """
    try:
        if sys.platform != "win32" or not getattr(sys, "frozen", False):
            return False
        if paths.is_portable():
            return False
        location = _installed_location()
        if location is None:
            return False
        return _same_folder(location, Path(sys.executable).parent)
    except Exception:  # noqa: BLE001 — any doubt means "use the release page"
        return False


def _check_tag(tag: str) -> str:
    if not _TAG_RE.fullmatch(tag or ""):
        raise DownloadError(f"'{tag}' isn't a release tag ALPACKA can update to.")
    return tag


def installer_name(version: str) -> str:
    """The setup's file name, as `.github/workflows/release.yml` publishes it."""
    return f"ALPACKA-{version}-windows-x64-setup.exe"


def asset_url(tag: str, name: str) -> str:
    """Download URL of a release file, always under this repo."""
    return f"https://github.com/{_REPO}/releases/download/{_check_tag(tag)}/{name}"


def parse_checksums(text: str) -> dict[str, str]:
    """`{file name: lowercase sha256}` from a `sha256sum`-style file."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts
        name = name.strip().lstrip("*")  # `*` marks binary mode in sha256sum output
        if _SHA256_RE.fullmatch(digest) and name:
            sums[name] = digest.lower()
    return sums


def updates_dir() -> Path:
    folder = paths.cache_root() / "updates"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def install_log_path() -> Path:
    return updates_dir() / "install.log"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download_installer(
    info: UpdateInfo,
    *,
    on_progress: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> Path:
    """Download the release's Windows setup and check it. Returns its path.

    The setup must be listed in the same release's `SHA256SUMS-windows.txt`
    and match it. A good copy from an earlier attempt is reused; older
    setups in the cache are removed. Raises `DownloadError`.
    """
    tag = _check_tag(info.tag)
    name = installer_name(tag[1:])
    folder = updates_dir()

    sums_file = folder / CHECKSUMS_NAME
    downloads.fetch(
        asset_url(tag, CHECKSUMS_NAME),
        sums_file,
        timeout=_DOWNLOAD_TIMEOUT,
        should_cancel=should_cancel,
    )
    try:
        text = sums_file.read_text(encoding="utf-8", errors="replace")
    finally:
        sums_file.unlink(missing_ok=True)
    expected = parse_checksums(text).get(name)
    if not expected:
        raise DownloadError(
            f"Release {tag} doesn't publish a checksum for {name}, so ALPACKA "
            "won't install it. Download it from the release page instead."
        )

    for old in folder.glob("ALPACKA-*-setup.exe*"):
        if old.name != name:
            try:
                old.unlink()
            except OSError:
                pass

    dest = folder / name
    if dest.is_file() and _sha256_file(dest) == expected:
        if on_progress:
            size = dest.stat().st_size
            on_progress(downloads.DownloadProgress(downloaded=size, total=size))
        return dest

    hasher = hashlib.sha256()
    downloads.fetch(
        asset_url(tag, name),
        dest,
        timeout=_DOWNLOAD_TIMEOUT,
        on_progress=on_progress,
        should_cancel=should_cancel,
        hasher=hasher,
    )
    if hasher.hexdigest() != expected:
        dest.unlink(missing_ok=True)
        raise DownloadError(
            "The downloaded installer didn't match the checksum published with "
            "the release, so ALPACKA deleted it. Try again later, or download it "
            "from the release page."
        )
    return dest


def installer_command(setup: Path, *, wait_pid: int, log_path: Path) -> list[str]:
    """The silent-update command line. `installer/alpacka.iss` reads the last two.

    `/waitpid=` makes the setup wait for ALPACKA to exit before it replaces
    any file; `/relaunch=1` makes it open ALPACKA again afterwards.
    """
    return [
        str(setup),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        f"/LOG={log_path}",
        f"/waitpid={int(wait_pid)}",
        "/relaunch=1",
    ]


def start_installer(cmd: list[str]) -> None:
    """Start the setup so it outlives ALPACKA. No shell; Windows only.

    It's started outside ALPACKA's job object when Windows allows it, so a
    launcher that kills its job on exit doesn't take the setup down too.
    """
    if sys.platform != "win32":
        raise DownloadError("ALPACKA only installs updates itself on Windows.")
    try:
        subprocess.Popen(
            cmd,
            close_fds=True,
            creationflags=_CREATE_NEW_PROCESS_GROUP | _CREATE_BREAKAWAY_FROM_JOB,
        )
        return
    except OSError:
        pass  # this job doesn't allow breakaway; start the setup inside it
    try:
        subprocess.Popen(cmd, close_fds=True, creationflags=_CREATE_NEW_PROCESS_GROUP)
    except OSError as exc:
        raise DownloadError(f"Couldn't start the installer: {exc}") from exc


# How an update went is only known at the next start: either the new
# version is running, or the old one is still here.


@dataclass
class PendingResult:
    updated: bool  # True if this run is the version the update installed
    version: str   # the version the update tried to install
    log: str       # the setup's log file


def _pending_path() -> Path:
    return paths.cache_root() / "updates" / "pending.json"


def record_pending(info: UpdateInfo, log_path: Path) -> None:
    updates_dir()
    fsutil.write_text_atomic(
        _pending_path(),
        json.dumps({"from": __version__, "to": info.version, "log": str(log_path)}),
    )


def clear_pending() -> None:
    try:
        _pending_path().unlink(missing_ok=True)
    except OSError:
        pass


def take_pending_result() -> PendingResult | None:
    """How the last update went, reported once; `None` if none was started."""
    path = _pending_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        data = None
    clear_pending()
    if not isinstance(data, dict) or not data.get("to"):
        return None
    version = str(data["to"])
    return PendingResult(
        updated=compare_versions(__version__, version) >= 0,
        version=version,
        log=str(data.get("log") or ""),
    )


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------


class _CheckSignals(QObject):
    """Owned by a runnable — Qt's QRunnable isn't a QObject itself."""

    finished = Signal(object)  # UpdateInfo | None


class _CheckRunnable(QRunnable):
    def __init__(self, timeout: float) -> None:
        super().__init__()
        self.signals = _CheckSignals()
        self._timeout = timeout

    def run(self) -> None:
        try:
            info = check_latest(timeout=self._timeout)
        except Exception:  # noqa: BLE001 — network code; nothing propagates to UI
            info = None
        self.signals.finished.emit(info)


def start_check(
    on_result: Callable[[UpdateInfo | None], None],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> None:
    """Kick off a background check on Qt's global thread pool.

    ``on_result`` is invoked with the :class:`UpdateInfo` (or ``None``)
    on the thread that owns the connection — usually the main thread
    when called from a widget slot. Safe to call from the UI thread.
    """
    runnable = _CheckRunnable(timeout=timeout)
    runnable.signals.finished.connect(on_result)
    QThreadPool.globalInstance().start(runnable)
