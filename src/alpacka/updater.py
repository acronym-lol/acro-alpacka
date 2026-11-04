"""GitHub Releases update checker.

Queries ``https://api.github.com/repos/acronym-lol/acro-alpacka/releases/latest``,
compares the tag against :data:`alpacka.__version__`, and surfaces an
:class:`UpdateInfo` if a newer release exists.

No in-place install: the Settings view and startup prompt both direct
the user to the GitHub release page via :func:`QDesktopServices.openUrl`.
Replacing the running executable in place isn't implemented.

Design notes:
  * Uses ``urllib.request`` (already used by ``td_download.py``) so the
    app stays PySide6-only + stdlib. No new dependencies.
  * The network call runs on the Qt thread pool via :func:`start_check`.
    Callers receive the result on the thread that owned the signal
    connection — typically the main thread, so UI handlers are safe.
  * Network or parse failures collapse to ``None`` (treated as "no
    update available"). The updater is best-effort by design; a flaky
    connection must not interrupt launch.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from . import __version__

_API_URL = "https://api.github.com/repos/acronym-lol/acro-alpacka/releases/latest"
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


RELEASES_URL = "https://github.com/acronym-lol/acro-alpacka/releases"


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
# Asset naming (for a future in-place updater; exported now so
# tests + the Settings view can reason about which asset a user would
# download for their platform).
# ---------------------------------------------------------------------------


def preferred_asset_substring() -> str:
    """Fragment the primary release asset's filename should contain.

    Matches the artifact names produced by ``.github/workflows/release.yml``.
    On Windows we point at the installer (``-setup.exe``); on macOS the DMG.
    """
    if sys.platform == "win32":
        return "windows-x64-setup"
    if sys.platform == "darwin":
        return "macos"
    return ""


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
