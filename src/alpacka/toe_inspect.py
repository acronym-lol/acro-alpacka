"""Extract TouchDesigner version metadata from a `.toe` file.

Shells out to `toeexpand -b` using a detected TD install. The `-b` flag
prints build info to stdout (without producing the full expanded .dir
folder) in a simple `key value` format:

    version 099
    build 2025.32280
    time Fri Apr 17 13:52:58 2026
    osname Windows
    osversion 10
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import td_install
from .settings import TDInstall


class ToeInspectError(RuntimeError):
    pass


@dataclass
class ToeInfo:
    version: str  # major line, e.g. "099"
    build: str  # year.build string, e.g. "2025.32280"


def read(toe_path: Path, install: TDInstall | None = None) -> ToeInfo:
    """Read the version / build fields from a .toe using `toeexpand -b`.

    If `install` is omitted, the newest detected TD install is used. Raises
    ToeInspectError if no TD install is available or parsing fails.
    """
    toe_path = Path(toe_path)
    if not toe_path.is_file():
        raise ToeInspectError(f"Not a file: {toe_path}")

    if install is None:
        installs = td_install.detect()
        install = td_install.newest(installs)
    if install is None:
        raise ToeInspectError(
            "No TouchDesigner installation detected — install TD to import existing projects."
        )

    exe = td_install.toeexpand_path(install)
    if not exe.is_file():
        raise ToeInspectError(f"toeexpand not found at {exe}")

    try:
        result = subprocess.run(
            [str(exe), "-b", str(toe_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToeInspectError(f"toeexpand timed out reading {toe_path}") from exc

    # toeexpand -b returns non-zero even on success; validate via stdout instead.
    try:
        return _parse_output(result.stdout)
    except ToeInspectError:
        stderr = (result.stderr or "").strip()
        if stderr:
            raise ToeInspectError(
                f"toeexpand failed (exit {result.returncode}): {stderr}"
            )
        raise


def _parse_output(stdout: str) -> ToeInfo:
    values: dict[str, str] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        key, _, value = line.partition(" ")
        if key and value:
            values[key.strip()] = value.strip()

    version = values.get("version", "").strip()
    build = values.get("build", "").strip()

    if not version or not build:
        raise ToeInspectError(
            f"Could not parse version/build from toeexpand output: {values!r}"
        )

    return ToeInfo(version=version, build=build)
