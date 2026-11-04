"""Subprocess runners for each `output_mode`.

`launch._spawn` resolves the project's effective output mode and
dispatches to one of:

  - :func:`run_silent`  — current behavior; no stdio params, output orphaned
    in packaged builds (matches what TD launches do)
  - :func:`run_console` — wrap the command in an external OS terminal so
    the user gets a real TTY. Pause-on-error keeps crash output visible.
  - :func:`run_inline`  — pipe stdout/stderr so a capture worker (in
    `output_manager`) can stream lines into a per-project ring buffer

Each runner returns the spawned :class:`subprocess.Popen`. The PID it
exposes is what gets registered in `running_processes`:

  - silent + inline: real child PID
  - console: the wrapper process's PID (cmd.exe on Windows, the launched
    bash script on macOS, the terminal emulator on Linux). Liveness then
    follows the wrapper, and `Stop` terminates it which kills its child.

The module has no Qt dependencies — output capture wiring lives in
`output_manager`. Keep it that way so headless / scripted callers can
import this without dragging in PySide6.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable

import psutil

# A POSIX shell variable name; anything else is left out of exported env.
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Windows creation-flag constants (defined on subprocess only on win32).
# Pulled into module scope for type-checker friendliness when this file
# is imported on POSIX hosts (the constants are unused there).
_CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


class ConsoleUnavailableError(RuntimeError):
    """No terminal emulator could be located for `run_console`.

    Caller (launch.py) catches this and falls back to inline output mode
    after surfacing a one-line notice. Currently only raised on Linux
    when the fallback chain (x-terminal-emulator → gnome-terminal →
    konsole → xterm) yields nothing.
    """


def _resolve_argv0(argv: list[str]) -> list[str]:
    """Return `argv` with `argv[0]` resolved to its full path on PATH.

    Same idea as the resolution applied in `template_scaffold.run_subprocess`
    — Windows ``CreateProcess`` (used by `Popen` with shell=False) doesn't
    honour ``PATHEXT``, so a bare ``"npm"`` fails when the actual binary
    is ``npm.cmd``. ``shutil.which`` consults PATHEXT and returns the
    matched filename (e.g. ``C:\\nvm4w\\nodejs\\npm.CMD``).

    On POSIX this is mostly a no-op since execvp already does PATH
    lookup; resolving still works the same way (returns the full path)
    and keeps argv-handling identical across platforms. Pass-through
    when argv is empty or shell=True (the shell does its own lookup),
    or when the first token isn't found on PATH (let Popen surface the
    same FileNotFoundError it would have anyway).
    """
    if not argv:
        return argv
    resolved = shutil.which(argv[0])
    if resolved is None:
        return argv
    return [resolved, *argv[1:]]


# ---------------------------------------------------------------------------
# Silent — current behavior, kept for parity
# ---------------------------------------------------------------------------


def run_silent(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    shell: bool,
) -> subprocess.Popen:
    """Spawn the process with inherited stdio (the legacy behavior).

    In a packaged windowed build the inherited stdio goes nowhere — that
    matches the existing pre-output_mode behavior and is the right
    default for kinds whose process owns its own window (TD, executable
    GUI apps).
    """
    if not shell:
        argv = _resolve_argv0(argv)
    return subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=env,
        shell=shell,
    )


# ---------------------------------------------------------------------------
# Inline — captured into a pipe; output_manager wires the buffer
# ---------------------------------------------------------------------------


def run_inline(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    shell: bool,
) -> subprocess.Popen:
    """Spawn with stdout+stderr redirected to a single text pipe.

    `PYTHONUNBUFFERED=1` is injected so Python scripts don't full-buffer
    when stdout isn't a TTY (otherwise users would see nothing for
    minutes). Node and most other runtimes detect non-TTY stdout and
    line-buffer correctly without help.

    Process-group flags scaffold a future graceful-stop: on Windows
    `CREATE_NEW_PROCESS_GROUP` enables `CTRL_BREAK_EVENT`; on POSIX
    `start_new_session=True` lets `os.killpg(SIGTERM)` reach the whole
    tree (e.g. an `npm run dev` parent + its node child).
    """
    if not shell:
        argv = _resolve_argv0(argv)
    final_env = {**env, "PYTHONUNBUFFERED": "1"}
    creation_flags = _CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    return subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=final_env,
        shell=shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,                  # line-buffered on the read side
        encoding="utf-8",
        errors="replace",
        creationflags=creation_flags if sys.platform == "win32" else 0,
        start_new_session=(sys.platform != "win32"),
    )


# ---------------------------------------------------------------------------
# Console — platform-specific terminal wrappers
# ---------------------------------------------------------------------------


def run_console(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    shell: bool,
) -> subprocess.Popen:
    """Launch the command inside a fresh OS terminal window.

    The wrapper pauses on non-zero exit so a crashing process leaves its
    error output visible. The PID returned belongs to the wrapper, not
    the inner command — see module docstring for the lifecycle contract.
    """
    if sys.platform == "win32":
        return _run_console_windows(argv, cwd=cwd, env=env, shell=shell)
    if sys.platform == "darwin":
        return _run_console_macos(argv, cwd=cwd, env=env, shell=shell)
    return _run_console_linux(argv, cwd=cwd, env=env, shell=shell)


# --- Windows ---------------------------------------------------------------


def _run_console_windows(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    shell: bool,
) -> subprocess.Popen:
    """Open a new console and run ``argv`` inside it, pausing on error.

    Quoting subtlety — there are *two* layers of quoting we have to keep
    out of each other's way:

      1. ``inner`` (built by `subprocess.list2cmdline`) wraps any argv
         token that contains spaces in ``"..."`` so cmd can identify
         the executable. For projects with spaces in their path (e.g.
         "Test Python CLI 3"), the venv python's full path ends up
         quoted: ``"C:\\Projects\\Test Python CLI 3\\.venv\\Scripts\\python.exe"``.
      2. The wrapper appends `` & if errorlevel 1 pause`` so a non-zero
         exit leaves the console open. The presence of ``&`` is a
         "special character" that defeats cmd's normal /c behavior of
         preserving quotes around an executable name.

    Combine those two and pass the result as a list to ``Popen``, and
    Popen runs ``list2cmdline`` again on the OUTER list — which double-
    escapes the inner quotes (``\\"…\\"``) and breaks cmd's command-name
    resolution. We dodge that by:

      - passing the full command line as a string (no outer list2cmdline);
      - using ``/s`` so cmd unconditionally strips the outermost ``"`` pair
        without consulting the "is there an &" rule.
    """
    inner = _shell_inner_string_windows(argv, shell=shell)
    # `cmd /s /c "<inner> & if errorlevel 1 pause"` — /s forces simple
    # outermost-quote stripping. Inner quotes around the executable
    # path survive, so cmd locates `python.exe` correctly even when
    # the project path contains spaces.
    cmd_line = f'cmd /s /c "{inner} & if errorlevel 1 pause"'
    return subprocess.Popen(
        cmd_line,
        cwd=str(cwd),
        env=env,
        creationflags=_CREATE_NEW_CONSOLE | _CREATE_NEW_PROCESS_GROUP,
    )


def _shell_inner_string_windows(argv: list[str], *, shell: bool) -> str:
    """Build a cmd-friendly command string from argv.

    For `shell=True`, argv[0] is already a full shell string (per the
    `RunConfig.shell` contract in `kinds/base.py`). For `shell=False`,
    every argument is quoted the way Windows programs parse it — and,
    unlike `subprocess.list2cmdline`, also when it contains a cmd
    operator (``& | < > ^ ( )``). This string runs inside ``cmd /c``, and
    an unquoted ``&`` there would start a second command. (cmd still
    expands ``%NAME%`` inside quotes; there's no way to escape that.)
    """
    if shell:
        return argv[0] if argv else ""
    return " ".join(_quote_windows_arg(a) for a in argv)


_CMD_NEEDS_QUOTES = frozenset(' \t"&|<>^()')


def _quote_windows_arg(arg: str) -> str:
    """Quote one argument for the MSVC command-line parser, cmd-safe."""
    if arg and not any(c in _CMD_NEEDS_QUOTES for c in arg):
        return arg
    out = ['"']
    backslashes = 0
    for c in arg:
        if c == "\\":
            backslashes += 1
        elif c == '"':
            out.append("\\" * (backslashes * 2 + 1) + '"')
            backslashes = 0
        else:
            out.append("\\" * backslashes + c)
            backslashes = 0
    out.append("\\" * (backslashes * 2) + '"')
    return "".join(out)


# --- macOS -----------------------------------------------------------------


def _run_console_macos(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    shell: bool,
) -> "subprocess.Popen | _AdoptedProcess":
    """Open the command in Terminal.app via a temp `.command` script.

    `open -a Terminal <script>` returns an `open` helper PID that exits
    in milliseconds — useless for liveness. To track the real shell
    running the user's command, the script writes its own ``$$`` to a
    PID-file before exec'ing the inner command; we poll that file briefly,
    then wrap the resolved PID in :class:`_AdoptedProcess` so launch.py
    can use it like a regular Popen.
    """
    inner = _shell_inner_string_posix(argv, shell=shell)
    # Names that aren't valid shell variable names can't be exported (and
    # would otherwise be pasted into the script unquoted), so skip them.
    env_export = "\n".join(
        f"export {k}={shlex.quote(str(v))}"
        for k, v in env.items()
        if _ENV_NAME.fullmatch(str(k))
    )
    pid_fd, pid_file_str = tempfile.mkstemp(prefix="alpacka-pid-", suffix=".pid")
    os.close(pid_fd)
    pid_file = Path(pid_file_str)

    # The trailing `read` only fires on non-zero exit — a clean run
    # closes its Terminal tab without prompting. The script deletes itself
    # as soon as bash has opened it: it holds the environment (which can
    # include secrets from alpacka.local.yml), so it mustn't linger in /tmp.
    script = (
        "#!/bin/bash\n"
        'rm -f -- "$0"\n'
        f"echo $$ > {shlex.quote(str(pid_file))}\n"
        f"cd {shlex.quote(str(cwd))}\n"
        f"{env_export}\n"
        f"{inner}\n"
        "rc=$?\n"
        "if [ $rc -ne 0 ]; then\n"
        '  echo ""\n'
        '  read -n 1 -r -p "exit $rc — press any key to close..."\n'
        "fi\n"
    )
    script_path = _write_temp_script(script, suffix=".command")

    # `open` exits ~immediately; we don't care about its returncode here.
    subprocess.Popen(["open", "-a", "Terminal", str(script_path)])
    real_pid = _wait_for_pid_file(pid_file, timeout=5.0)
    try:
        pid_file.unlink()
    except OSError:
        pass
    if real_pid is None:
        # Terminal didn't get our script running in time. Surface as a
        # console-unavailable so the caller can fall back to inline.
        raise ConsoleUnavailableError(
            "Could not start a Terminal.app session; falling back to inline."
        )
    return _AdoptedProcess(real_pid)


def _wait_for_pid_file(path: Path, *, timeout: float) -> int | None:
    """Poll for `path` to contain a parseable PID. Returns None on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    return int(text)
        except (OSError, ValueError):
            pass
        time.sleep(0.05)
    return None


# --- Linux -----------------------------------------------------------------


_LINUX_TERMINAL_FALLBACKS = (
    "x-terminal-emulator",  # Debian/Ubuntu meta-pointer
    "gnome-terminal",
    "konsole",
    "xfce4-terminal",
    "tilix",
    "alacritty",
    "kitty",
    "xterm",
)


def _run_console_linux(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    shell: bool,
) -> subprocess.Popen:
    """Locate a terminal emulator and run the command inside it.

    Raises :class:`ConsoleUnavailableError` if nothing on the fallback
    chain resolves — the caller falls back to inline mode.
    """
    terminal = _find_linux_terminal()
    if terminal is None:
        raise ConsoleUnavailableError(
            "No terminal emulator found on PATH. Tried: "
            + ", ".join(_LINUX_TERMINAL_FALLBACKS)
        )

    inner = _shell_inner_string_posix(argv, shell=shell)
    bash_command = (
        f"cd {shlex.quote(str(cwd))} && "
        f"{inner}; "
        "rc=$?; "
        'if [ $rc -ne 0 ]; then '
        '  echo; '
        '  read -n 1 -r -p "exit $rc — press any key to close..."; '
        "fi"
    )

    # `--` separator is the most consistent across emulators; `-e` is
    # deprecated in gnome-terminal 3.x and quoting differs per impl.
    # We pass `bash -c <command>` so each emulator just exec's it.
    wrapper = [terminal, "--", "bash", "-c", bash_command]
    return subprocess.Popen(
        wrapper,
        env=env,
        # `start_new_session=True` so the terminal lives in its own
        # session; closing ALPACKA shouldn't reach into it.
        start_new_session=True,
    )


def _find_linux_terminal() -> str | None:
    for name in _LINUX_TERMINAL_FALLBACKS:
        path = shutil.which(name)
        if path:
            return path
    return None


# ---------------------------------------------------------------------------
# POSIX inner-string builder (shared by macOS + Linux)
# ---------------------------------------------------------------------------


def _shell_inner_string_posix(argv: list[str], *, shell: bool) -> str:
    """Build a POSIX-shell command string from argv.

    For `shell=True`, argv[0] is already a shell command string. For
    `shell=False`, shlex-quote each token so the resulting string parses
    back to the same argv when handed to bash.
    """
    if shell:
        return argv[0] if argv else ""
    return " ".join(shlex.quote(a) for a in argv)


def _write_temp_script(contents: str, *, suffix: str) -> Path:
    """Write `contents` to a unique temp file, executable by its owner only.

    It can contain the project's environment (secrets included), so it's
    0700 and the scripts ALPACKA writes delete themselves when they start.
    """
    fd, path_str = tempfile.mkstemp(prefix="alpacka-launch-", suffix=suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(contents)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    path = Path(path_str)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def linux_terminal_available() -> bool:
    """Quick preflight callers can use to gate UI before launch."""
    return _find_linux_terminal() is not None


# ---------------------------------------------------------------------------
# Adopted-process wrapper (macOS console mode)
# ---------------------------------------------------------------------------


class _AdoptedProcess:
    """Popen look-alike for a PID we located but did not directly fork.

    Used on macOS when a `.command` script we hand off to Terminal.app
    writes its own ``$$`` and we resolve that PID after the fact. We
    expose the minimum API surface that launch.py + process_tracker
    need: ``pid``, ``poll``, ``wait``, ``returncode``, ``terminate``,
    ``kill``. Everything routes through psutil so the OS PID is the
    only state we hold.
    """

    def __init__(self, pid: int) -> None:
        self.pid = int(pid)
        self.returncode: int | None = None
        # Hold the psutil handle so .wait() can race-free observe exit.
        try:
            self._proc: psutil.Process | None = psutil.Process(self.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            self._proc = None
            self.returncode = 0  # already gone

    def poll(self) -> int | None:
        """Match Popen.poll(): None while running, exit code when done."""
        if self.returncode is not None:
            return self.returncode
        if self._proc is None or not self._proc.is_running():
            self.returncode = 0
            return 0
        return None

    def wait(self, timeout: float | None = None) -> int:
        """Block until the process exits. Real exit code is unknowable
        through psutil — we report ``0`` so callers see a successful
        completion. Console-mode UX doesn't surface exit codes anyway.
        """
        if self.returncode is not None:
            return self.returncode
        if self._proc is None:
            self.returncode = 0
            return 0
        try:
            self._proc.wait(timeout=timeout)
        except psutil.TimeoutExpired:
            raise subprocess.TimeoutExpired(cmd="<adopted>", timeout=timeout)
        except psutil.NoSuchProcess:
            pass
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    def kill(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


__all__ = [
    "ConsoleUnavailableError",
    "run_silent",
    "run_console",
    "run_inline",
    "linux_terminal_available",
]
