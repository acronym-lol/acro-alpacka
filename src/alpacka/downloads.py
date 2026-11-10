"""Download a file over HTTPS, with progress, cancel and an optional hash.

Shared by the TouchDesigner installer download (`td_download`) and the
ALPACKA updater (`updater`). Qt-free; callers run it on a worker thread.
"""
from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_CHUNK = 1024 * 256  # 256 KB


class DownloadError(RuntimeError):
    pass


@dataclass
class DownloadProgress:
    downloaded: int
    total: int  # 0 if unknown


ProgressCallback = Callable[[DownloadProgress], None]


def fetch(
    url: str,
    dest: Path,
    *,
    timeout: float,
    on_progress: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
    hasher: Any = None,
) -> None:
    """Download `url` to `dest`.

    The data goes to `<dest>.part` first and is renamed only once complete,
    so `dest` never holds half a file. A download that ends up anywhere
    other than an `https://` URL after redirects is refused. `hasher` (a
    `hashlib` object) is fed every chunk as it arrives. Raises
    `DownloadError`, with the message "Cancelled" when `should_cancel`
    returned True.
    """
    tmp = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            if not str(resp.geturl()).startswith("https://"):
                raise DownloadError(f"Refusing a download redirected to {resp.geturl()}")
            total = int(resp.headers.get("Content-Length", "0") or 0)
            downloaded = 0
            with tmp.open("wb") as f:
                while True:
                    if should_cancel and should_cancel():
                        raise DownloadError("Cancelled")
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    if hasher is not None:
                        hasher.update(chunk)
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


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
