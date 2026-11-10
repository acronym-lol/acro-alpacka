"""Platform-specific config paths for the launcher.

Honors portable mode: if a `portable.txt` file exists next to the running
executable (or, in dev, next to the launcher/ directory), config is routed
to `./data/` instead of the per-user OS location.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _exe_dir() -> Path:
    """Directory containing the running executable (frozen) or the repo root (dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # Dev mode: src/alpacka/paths.py -> repo root.
    return Path(__file__).resolve().parents[2]


def is_portable() -> bool:
    return (_exe_dir() / "portable.txt").exists()


def config_root() -> Path:
    """Root directory for launcher-owned state (DB, settings, caches)."""
    if is_portable():
        return _exe_dir() / "data"

    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "ALPACKA"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ALPACKA"

    # Linux fallback (not officially supported in v1 but avoids crashes during dev)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "alpacka"


def cache_root() -> Path:
    """Root directory for transient caches (downloads, thumbnails)."""
    if is_portable():
        return _exe_dir() / "data" / "cache"

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ALPACKA" / "Cache"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ALPACKA"

    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "alpacka"


def ensure_dirs() -> None:
    """Create config and cache dirs if missing."""
    config_root().mkdir(parents=True, exist_ok=True)
    cache_root().mkdir(parents=True, exist_ok=True)


def db_path() -> Path:
    return config_root() / "projects.db"


def settings_path() -> Path:
    return config_root() / "settings.json"


def acro_td_core_dir() -> Path | None:
    """Resolve the ACRO_TD_CORE env var to a Path, or None if unset/missing."""
    val = os.environ.get("ACRO_TD_CORE")
    if not val:
        return None
    p = Path(val).expanduser()
    return p if p.exists() else None
