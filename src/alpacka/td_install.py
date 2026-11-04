"""Detect installed TouchDesigner builds on the current machine.

Windows: enumerate `HKEY_CLASSES_ROOT` subkeys for file-association entries
with names like `TouchDesigner.2025.32280`, then read each key's
`shell\\open\\command` value to find the `.exe` path.

macOS: glob `/Applications/TouchDesigner*.app` and read `CFBundleVersion`
from each bundle's `Info.plist`.

The detected list is the source of truth for the Settings UI; each entry
is also serialized into `settings.json → td_installs` so the registry
scan can be skipped on cold start.
"""
from __future__ import annotations

import glob
import os
import plistlib
import re
import sys
from pathlib import Path

from .settings import TDInstall


# Modern TD builds are all the "099" line. Older builds ("088") have not
# been shipped since 2017 and aren't in scope.
_DEFAULT_MAJOR = "099"


_VERSION_RE = re.compile(r"\d{4}\.\d{4,6}")


def is_valid_version(version: str) -> bool:
    """True for a TouchDesigner build number like ``2025.32460``.

    The pinned version comes from a project's alpacka.yml, which can come
    from anywhere, and it ends up in a download URL, a file name and a
    command line. Anything that isn't plainly ``YYYY.NNNNN`` is refused.
    """
    return bool(_VERSION_RE.fullmatch(str(version or "")))


def detect() -> list[TDInstall]:
    if sys.platform == "win32":
        return _detect_windows()
    if sys.platform == "darwin":
        return _detect_macos()
    return []


def _detect_windows() -> list[TDInstall]:
    import winreg  # Windows-only

    installs: list[TDInstall] = []
    seen_paths: set[str] = set()

    try:
        reg = winreg.ConnectRegistry(None, winreg.HKEY_CLASSES_ROOT)
    except OSError:
        return installs

    try:
        i = 0
        while True:
            try:
                key_name = winreg.EnumKey(reg, i)
            except OSError:
                break
            i += 1

            if "TouchDesigner" not in key_name:
                continue
            last_segment = key_name.rsplit(".", 1)[-1]
            if not last_segment.isdigit():
                continue

            try:
                cmd = winreg.QueryValue(reg, f"{key_name}\\shell\\open\\command")
            except OSError:
                continue

            exe_path = _extract_quoted_path(cmd)
            if not exe_path or exe_path in seen_paths:
                continue
            if not Path(exe_path).is_file():
                continue
            seen_paths.add(exe_path)

            version = _version_from_key_name(key_name)
            installs.append(
                TDInstall(version=version, major=_DEFAULT_MAJOR, path=exe_path)
            )
    finally:
        try:
            reg.Close()
        except Exception:  # noqa: BLE001
            pass

    installs.sort(key=_version_sort_key, reverse=True)
    return installs


def _extract_quoted_path(cmd: str) -> str:
    """Extract the first double-quoted substring from a command line string."""
    parts = cmd.split('"')
    if len(parts) >= 2:
        return parts[1]
    # Fallback for unquoted commands: take the first whitespace-separated token.
    token = cmd.strip().split()[0] if cmd.strip() else ""
    return token


def _version_from_key_name(key_name: str) -> str:
    """`TouchDesigner.2025.32280` -> `2025.32280`."""
    _, _, tail = key_name.partition(".")
    return tail or key_name


def _detect_macos() -> list[TDInstall]:
    installs: list[TDInstall] = []
    for app_path in glob.glob("/Applications/TouchDesigner*.app"):
        info_plist = os.path.join(app_path, "Contents", "Info.plist")
        if not os.path.isfile(info_plist):
            continue
        try:
            with open(info_plist, "rb") as f:
                data = plistlib.load(f)
        except (plistlib.InvalidFileException, OSError):
            continue

        bundle_version = data.get("CFBundleVersion") or data.get(
            "CFBundleShortVersionString"
        )
        if not bundle_version:
            continue

        version = _canonical_mac_version(bundle_version)
        installs.append(
            TDInstall(version=version, major=_DEFAULT_MAJOR, path=app_path)
        )

    installs.sort(key=_version_sort_key, reverse=True)
    return installs


def _canonical_mac_version(bundle_version: str) -> str:
    """`2025.32280.1` -> `2025.32280` (drop the patch field)."""
    parts = bundle_version.split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return bundle_version


def _version_sort_key(install: TDInstall) -> tuple[int, ...]:
    pieces = []
    for chunk in install.version.split("."):
        try:
            pieces.append(int(chunk))
        except ValueError:
            pieces.append(0)
    return tuple(pieces)


def toeexpand_path(install: TDInstall) -> Path:
    """Resolve the `toeexpand` binary shipped alongside a given TD install."""
    if sys.platform == "win32":
        exe = Path(install.path)
        # exe lives at <install>/bin/TouchDesigner.exe → sibling is bin/toeexpand.exe
        return exe.parent / "toeexpand.exe"
    if sys.platform == "darwin":
        return Path(install.path) / "Contents" / "MacOS" / "toeexpand"
    raise RuntimeError(f"Unsupported platform: {sys.platform}")


def launch_command(install: TDInstall, toe_path: Path) -> list[str]:
    """Return the subprocess argv for opening a `.toe` with this install.

    On macOS we intentionally invoke the Mach-O binary inside the .app
    bundle directly (rather than `open -a`). That's the only way to get
    back TD's real PID from ``Popen`` — ``open`` is a short-lived helper
    whose PID has nothing to do with TD itself.
    """
    if sys.platform == "win32":
        return [install.path, str(toe_path)]
    if sys.platform == "darwin":
        app_path = Path(install.path)
        binary = app_path / "Contents" / "MacOS" / "TouchDesigner"
        return [str(binary), str(toe_path)]
    raise RuntimeError(f"Unsupported platform: {sys.platform}")


def newest(installs: list[TDInstall]) -> TDInstall | None:
    return installs[0] if installs else None


def find_by_version(installs: list[TDInstall], version: str) -> TDInstall | None:
    for i in installs:
        if i.version == version:
            return i
    return None


def newest_at_least(installs: list[TDInstall], version: str) -> TDInstall | None:
    target = _version_sort_key_str(version)
    candidates = [i for i in installs if _version_sort_key(i) >= target]
    return candidates[0] if candidates else None


def _version_sort_key_str(version: str) -> tuple[int, ...]:
    pieces = []
    for chunk in version.split("."):
        try:
            pieces.append(int(chunk))
        except ValueError:
            pieces.append(0)
    return tuple(pieces)
