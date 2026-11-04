"""PyInstaller build script for ALPACKA.

Usage:
    python build.py

Produces a portable folder build at ``dist/ALPACKA/``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
PKG = ROOT / "src" / "alpacka"


def clean() -> None:
    for p in (DIST, BUILD):
        if p.exists():
            shutil.rmtree(p)


def _icon_flag() -> list[str]:
    """Platform-appropriate --icon flag for PyInstaller, if the file exists."""
    name = "alpacka.icns" if sys.platform == "darwin" else "alpacka.ico"
    icon = PKG / "resources" / "icons" / name
    return ["--icon", str(icon)] if icon.is_file() else []


def _manifest_flag() -> list[str]:
    """Embed the per-monitor-v2 DPI-awareness manifest (Windows only).

    Without a DPI-aware manifest the frozen exe mis-maps mouse input on
    scaled displays (125/150/175%); see ALPACKA.manifest and
    app.py:_ensure_dpi_awareness. No-op on non-Windows / if the file is
    missing.
    """
    if sys.platform != "win32":
        return []
    manifest = ROOT / "ALPACKA.manifest"
    return ["--manifest", str(manifest)] if manifest.is_file() else []


def _data_flags() -> list[str]:
    """``--add-data`` entries that bundle runtime resources.

    PyInstaller only copies ``.py`` files from a package by default; every
    non-Python asset the app reads at runtime (QSS, SVG icons, .ico, .icns,
    etc.) has to be added explicitly. The destination is the package-relative
    path inside the bundle so ``Path(__file__).parent / "resources"`` keeps
    working in both dev and packaged modes.
    """
    sep = os.pathsep
    return [
        "--add-data", f"{_staged_resources()}{sep}alpacka/resources",
        "--add-data", f"{PKG / 'ui' / 'style.qss'}{sep}alpacka/ui",
    ]


def _staged_resources() -> Path:
    """A copy of `resources/` without Python caches.

    The bundled project templates contain `.py` starter files; running or
    testing them in place leaves `__pycache__` folders that would otherwise
    ship inside the app (and be copied into users' new projects).
    """
    staged = BUILD / "staged-resources"
    if staged.exists():
        shutil.rmtree(staged)
    shutil.copytree(
        PKG / "resources",
        staged,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store", "Thumbs.db"),
    )
    return staged


# Distributions bundled into the app whose licence files ship with it
# (THIRD_PARTY_NOTICES.md lists them). Their files are taken from the
# installed packages so they always match the versions actually bundled.
_BUNDLED_DISTS = ("shiboken6", "psutil", "PyYAML", "ruamel.yaml", "pyinstaller")


def _licence_files(dist_name: str) -> list[Path]:
    from importlib import metadata

    try:
        dist = metadata.distribution(dist_name)
    except metadata.PackageNotFoundError:
        return []
    found = []
    for f in dist.files or []:
        name = f.name.upper()
        if not any(word in name for word in ("LICENSE", "LICENCE", "COPYING", "NOTICE")):
            continue
        if "COMMERCIAL" in name:  # Qt's commercial-licence stub; ALPACKA uses the LGPL
            continue
        path = Path(f.locate())
        if path.is_file():
            found.append(path)
    return found


def _python_licence() -> Path | None:
    base = Path(sys.base_prefix)
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    candidates = [
        base / "LICENSE.txt",  # python.org Windows installs
        base / "LICENSE",
        base / "LICENSE_PYTHON.txt",  # conda
        base / "Lib" / "LICENSE.txt",
        base / "lib" / version / "LICENSE.txt",  # macOS framework / Linux
    ]
    return next((c for c in candidates if c.is_file()), None)


def copy_notices(target: Path) -> None:
    """Put LICENSE, THIRD_PARTY_NOTICES.md and every licence text in `target`.

    Bundled libraries' licences must travel with the binaries (LGPL / BSD /
    MIT / PSF all require the notice), so they sit next to the app where
    users can find them, not buried in PyInstaller's internals.
    """
    target.mkdir(parents=True, exist_ok=True)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, target / name)
    licences = target / "licenses"
    licences.mkdir(exist_ok=True)
    for f in (ROOT / "licenses").iterdir():
        if f.is_file():
            shutil.copy2(f, licences / f.name)
    for dist_name in _BUNDLED_DISTS:
        for f in _licence_files(dist_name):
            shutil.copy2(f, licences / f"{dist_name}-{f.name}")
    py = _python_licence()
    if py is not None:
        shutil.copy2(py, licences / "Python-LICENSE.txt")


def main() -> int:
    clean()
    # Use the bootstrap shim (src/run_alpacka.py), not src/alpacka/__main__.py.
    # The frozen entry has no parent package, so relative imports in
    # __main__.py fail with "attempted relative import with no known
    # parent package". The shim uses an absolute import to work around it.
    entry = ROOT / "src" / "run_alpacka.py"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        "ALPACKA",
        "--windowed",
        "--noconfirm",
        "--onedir",
        "--paths",
        str(ROOT / "src"),
        *_icon_flag(),
        *_manifest_flag(),
        *_data_flags(),
        str(entry),
    ]
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc != 0:
        return rc
    copy_notices(DIST / "ALPACKA")
    app_bundle = DIST / "ALPACKA.app"
    if app_bundle.is_dir():
        copy_notices(app_bundle / "Contents" / "Resources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
