"""External tool integrations — open folder, VS Code, terminal, repo URL.

Cross-platform helpers used by the project card action buttons. Each
function raises ExternalToolError on failure so the UI can surface a
friendly message.
"""
from __future__ import annotations

import configparser
import os
import re
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path


class ExternalToolError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Git origin detection
# ---------------------------------------------------------------------------


def detect_git_remote_url(folder: Path) -> str | None:
    """Return the normalized browser URL for a project's `origin` remote,
    or None if the folder isn't a git repo or has no origin.

    Reads `.git/config` directly — no `git` CLI required.
    """
    folder = Path(folder)
    config_path = folder / ".git" / "config"
    if not config_path.is_file():
        return None

    parser = configparser.ConfigParser(strict=False)
    try:
        parser.read(config_path, encoding="utf-8")
    except (configparser.Error, OSError):
        return None

    # Git config sections look like: [remote "origin"]
    for section in parser.sections():
        if section.strip().lower() == 'remote "origin"':
            raw_url = parser[section].get("url")
            if raw_url:
                return _normalize_git_url(raw_url.strip())
    return None


def _normalize_git_url(url: str) -> str:
    """Convert any common git remote URL form to an https browser URL."""
    if url.endswith(".git"):
        url = url[:-4]
    url = url.rstrip("/")

    # ssh:// with explicit scheme: ssh://[user@]host[:port]/path
    if url.startswith("ssh://"):
        remainder = url[len("ssh://") :]
        if "@" in remainder:
            remainder = remainder.split("@", 1)[1]
        return "https://" + remainder

    # git:// protocol
    if url.startswith("git://"):
        return "https://" + url[len("git://") :]

    # Already http/https — leave as-is
    if url.startswith("http://") or url.startswith("https://"):
        return url

    # SSH shorthand: git@host:owner/repo (no scheme, colon separates host and path)
    ssh_match = re.match(r"^([\w.-]+@)?([\w.-]+):(?!\d)(.+)$", url)
    if ssh_match:
        _, host, path = ssh_match.groups()
        return f"https://{host}/{path}"

    return url


def open_repo_url(url: str) -> None:
    """Open a repository URL in the user's default browser."""
    if not url:
        raise ExternalToolError("No repository URL available.")

    # Guard against non-http schemes so we don't hand git@ or ssh:// URLs
    # to the OS as clickable links.
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ExternalToolError(
            f"Repository URL is not an http(s) URL: {url}"
        )

    try:
        if sys.platform == "win32":
            # os.startfile invokes the registered URL handler reliably,
            # where webbrowser.open can silently fail on some systems.
            os.startfile(url)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url])
    except OSError as exc:
        # Last-ditch fallback to webbrowser.
        try:
            if webbrowser.open(url, new=2):
                return
        except webbrowser.Error:
            pass
        raise ExternalToolError(f"Failed to open browser: {exc}") from exc


def open_folder(path: Path) -> None:
    """Reveal a folder in the OS file manager."""
    path = Path(path)
    if not path.exists():
        raise ExternalToolError(f"Folder does not exist: {path}")

    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def open_vscode(path: Path) -> None:
    """Open a folder in VS Code.

    Requires the ``code`` CLI to be on PATH (VS Code installs it via
    "Shell Command: Install 'code' in PATH" on macOS, and automatically
    on Windows).
    """
    path = Path(path)
    if not path.exists():
        raise ExternalToolError(f"Folder does not exist: {path}")

    cmd = _resolve_vscode_cmd()
    if cmd is None:
        raise ExternalToolError(
            "VS Code CLI (`code`) not found on PATH. Install VS Code or "
            "add its CLI to PATH from the Command Palette."
        )

    try:
        if sys.platform == "win32":
            # Prefer VS Code's own Code.exe: no shell, so nothing in the
            # folder name is ever interpreted. Only fall back to the
            # `code.cmd` shim (which needs cmd.exe) when Code.exe isn't
            # found; both arguments are quoted, and Windows folder names
            # can't contain `"`.
            exe = resolve_vscode_editor()
            if exe and exe.lower().endswith(".exe"):
                subprocess.Popen([exe, str(path)])
            else:
                subprocess.Popen(f'"{cmd}" "{path}"', shell=True)
        else:
            subprocess.Popen([cmd, str(path)])
    except OSError as exc:
        raise ExternalToolError(f"Failed to launch VS Code: {exc}") from exc


def open_terminal(path: Path) -> None:
    """Open a terminal window with the working directory set to `path`."""
    path = Path(path)
    if not path.exists():
        raise ExternalToolError(f"Folder does not exist: {path}")

    if sys.platform == "win32":
        _open_terminal_windows(path)
    elif sys.platform == "darwin":
        _open_terminal_macos(path)
    else:
        _open_terminal_linux(path)


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------


def _resolve_vscode_cmd() -> str | None:
    """Return the path to the `code` CLI, or None if not found."""
    for candidate in ("code", "code.cmd", "code-insiders", "code-insiders.cmd"):
        found = shutil.which(candidate)
        if found:
            return found

    # Windows: common install paths if CLI isn't on PATH.
    if sys.platform == "win32":
        for env in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            root = os.environ.get(env)
            if not root:
                continue
            for sub in (
                r"Programs\Microsoft VS Code\bin\code.cmd",
                r"Microsoft VS Code\bin\code.cmd",
            ):
                p = Path(root) / sub
                if p.is_file():
                    return str(p)
    return None


def resolve_vscode_editor() -> str | None:
    """Path to the VS Code editor itself (not the `code` CLI), or None.

    TouchDesigner's `dats.texteditor` preference needs the program TD
    starts to edit a DAT: `Code.exe` on Windows (one folder up from the
    CLI's `bin\\code.cmd`), the `.app` bundle on macOS.
    """
    if sys.platform == "win32":
        candidates: list[Path] = []
        cmd = _resolve_vscode_cmd()
        if cmd and Path(cmd).parent.name.lower() == "bin":
            install_dir = Path(cmd).parent.parent
            candidates += [install_dir / "Code.exe", install_dir / "Code - Insiders.exe"]
        for env in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            root = os.environ.get(env)
            if root:
                candidates += [
                    Path(root) / "Programs" / "Microsoft VS Code" / "Code.exe",
                    Path(root) / "Microsoft VS Code" / "Code.exe",
                ]
        for p in candidates:
            if p.is_file():
                return str(p)
        return None
    if sys.platform == "darwin":
        for root in (Path("/Applications"), Path.home() / "Applications"):
            app = root / "Visual Studio Code.app"
            if app.is_dir():
                return str(app)
    return None


def _open_terminal_windows(path: Path) -> None:
    # Prefer Windows Terminal if available.
    wt = shutil.which("wt.exe") or shutil.which("wt")
    if wt:
        try:
            subprocess.Popen([wt, "-d", str(path)])
            return
        except OSError:
            pass

    # Fall back to cmd.exe. /K keeps the terminal open. The folder is set
    # as the working directory rather than typed into a `cd` command, so
    # characters like `&` in a folder name are never run as commands.
    try:
        subprocess.Popen(
            ["cmd.exe", "/K"],
            cwd=str(path),
            creationflags=subprocess.CREATE_NEW_CONSOLE,  # type: ignore[attr-defined]
        )
    except OSError as exc:
        raise ExternalToolError(f"Failed to open terminal: {exc}") from exc


def _open_terminal_macos(path: Path) -> None:
    try:
        subprocess.Popen(["open", "-a", "Terminal", str(path)])
    except OSError as exc:
        raise ExternalToolError(f"Failed to open terminal: {exc}") from exc


def _open_terminal_linux(path: Path) -> None:
    for candidate in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
        found = shutil.which(candidate)
        if found:
            try:
                subprocess.Popen([found], cwd=str(path))
                return
            except OSError:
                continue
    raise ExternalToolError("No supported terminal emulator found on PATH.")
