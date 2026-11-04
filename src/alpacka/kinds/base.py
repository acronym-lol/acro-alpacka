"""Shared types and the `KindHandler` protocol.

Each "kind" of project (touchdesigner, nodejs, python, docker-compose,
executable, shell, generic) ships a handler that knows how to launch,
track liveness, focus, terminate, and detect projects of that kind.

This module is pure-Python with no side effects — it defines the types;
kinds/__init__.py holds the registry.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import psutil

if TYPE_CHECKING:
    from ..process_tracker import ProcessRecord
    from ..project import Project


@dataclass(frozen=True)
class LaunchSpec:
    """Everything `launch.py` needs to spawn a process for this kind.

    The handler builds one of these from `(project, run_config, merged_vars)`
    and returns it; the generic launcher does the actual `subprocess.Popen`.
    """

    argv: list[str]
    cwd: Path
    env: dict[str, str]                  # merged with os.environ by the caller
    shell: bool = False
    # Substring the process-liveness sweep checks for against psutil's
    # `proc.name()`. Empty string disables the name check (pid-only liveness).
    # This column is persisted per-row in `running_processes.process_name_pattern`
    # so the TouchDesigner invariant ("touchdesigner" substring) survives
    # across kinds without re-deriving from the kind name.
    process_name_pattern: str = ""
    run_config_name: str = "default"
    # Kind-generic analogue of TouchDesigner's toe_file — written into
    # running_processes.toe_file for back-compat with the existing schema.
    artifact_filename: str = ""


@dataclass
class RunConfig:
    """A named way to launch a project.

    Kinds provide a default config (TD's is "open the toe_file"); users can
    declare named alternatives in `alpacka.yml` (e.g. nodejs dev vs build).
    """

    name: str = "default"
    command: str | None = None           # template string with {VAR} placeholders
    args: list[str] = field(default_factory=list)
    cwd: str | None = None               # relative to project folder; None ⇒ folder itself
    shell: bool = False
    variables: dict[str, Any] = field(default_factory=dict)
    # Explicit env vars merged onto the spawned process's environment after
    # `variables` and any kind-specific structured settings. Power-user
    # escape hatch — e.g. set arbitrary `TOUCH_*` keys without UI support.
    # Keys/values are coerced to str at launch time.
    env: dict[str, str] = field(default_factory=dict)
    # Override the kind's default long-running disposition. None ⇒ inherit.
    # A nodejs "dev" config is long-running; "build" is one-shot.
    long_running: bool | None = None
    terminate_signal: str = "TERM"       # TERM | INT | KILL


@dataclass(frozen=True)
class InstallStep:
    """A single dependency-install command to run after project creation.

    Kinds return a list of these from `install_steps()`. Each step runs
    in sequence through the same subprocess runner used by scaffold
    commands (`template_scaffold.run_subprocess`), with live output
    streamed into the New Project dialog's progress UI.

    - ``argv`` — the command, already split. We don't shlex on the fly
      so handlers can produce platform-correct paths (e.g. on Windows,
      the venv pip lives at ``.venv\\Scripts\\pip.exe``).
    - ``label`` — human-readable description shown in the progress
      dialog header (e.g. "Create venv", "Install dependencies").
    - ``cwd`` — relative to the project folder. Defaults to the project
      folder itself.
    - ``requires_command`` — preflight: `shutil.which` must find this
      on PATH before the step runs. Empty string skips the check
      (useful when ``argv[0]`` is an absolute path the handler built).
    """

    argv: list[str]
    label: str = ""
    cwd: str = "."
    requires_command: str = ""


@dataclass(frozen=True)
class DetectResult:
    """Result of `handler.detect(folder)`.

    `signals` is a list of human-readable reasons ("found package.json") so
    the add-existing dialog can show *why* a folder was detected as a given
    kind. `suggested_config` pre-populates `kind_data.<kind>` when the user
    confirms adoption.
    """

    confidence: float                    # 0.0 ⇒ not this kind; 1.0 ⇒ definitely this kind
    signals: list[str] = field(default_factory=list)
    suggested_config: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class KindHandler(Protocol):
    """Every kind module exposes an instance of this protocol.

    Attribute-style metadata (not methods) covers facts that never vary
    per-invocation.
    """

    # --- Static metadata ---
    name: str                            # "touchdesigner"
    display_name: str                    # "TouchDesigner"
    # Basename of an SVG in `resources/icons/` representing the kind. Used
    # by the card-thumbnail placeholder when a project has no real
    # `.launcher-thumb.png`. Should be a Lucide-style line icon vendored
    # alongside the rest of the icon set.
    icon: str                            # "aperture", "code-2", …
    long_running: bool                   # default disposition for this kind's run_configs
    supports_versions: bool              # True ⇒ the UI shows a version picker
    default_process_name_pattern: str    # written into running_processes on launch
    # Recommended default for `Project.output_mode` when the user hasn't
    # picked one. Concrete values: "console" (cli kinds), "inline", "none"
    # (TD/executable — they own their own window/output).
    default_output_mode: str
    # Whether `install_steps()` may return non-empty results for typical
    # projects of this kind. The New Project dialog uses this as a
    # hint (before the project folder exists) to gate the "Install
    # dependencies after create" checkbox: True for Python and NodeJS,
    # False for kinds that have no dependency manifest.
    produces_install_steps: bool
    # Folders created at project-scaffold time by universal_bootstrap.apply().
    # Empty tuple ⇒ nothing beyond the baseline gitignore.
    universal_folders: tuple[str, ...]

    # --- Launch + lifecycle ---
    def build_launch(
        self,
        project: "Project",
        run_config: RunConfig,
        merged_vars: dict[str, Any],
    ) -> LaunchSpec: ...

    def default_run_config(self, project: "Project") -> RunConfig: ...

    def liveness(self, record: "ProcessRecord") -> bool: ...

    def focus(self, record: "ProcessRecord") -> bool: ...

    def terminate(self, record: "ProcessRecord", timeout: float) -> bool: ...

    # --- Post-create install (optional) ---
    def install_steps(self, project_folder: Path) -> list[InstallStep]:
        """Return commands to set up dependencies for a newly-created project.

        Inspected after `template_apply.apply()` completes (and after
        scaffold commands, if any). The New Project dialog gates the
        steps behind a single "Install dependencies after create"
        checkbox; failure of any step is non-fatal — the project stays
        registered so the user can retry manually. Empty list ⇒ skip
        the install phase entirely (and the checkbox is hidden).

        Implementations should detect their own preconditions (e.g.
        Python looks for ``pyproject.toml`` / ``requirements.txt``;
        NodeJS looks for ``package.json``) so a kind that ends up with
        an unexpected layout doesn't trigger spurious commands.
        """
        ...

    # --- Detection + card display ---
    def detect(self, folder: Path) -> DetectResult: ...

    def display_url(
        self, project: "Project", variables: dict[str, Any]
    ) -> str | None: ...

    def target_summary(self, project: "Project") -> str:
        """A short, human-readable summary of what Launch will do.

        Shown on the card's target row — e.g. ``"$ npm run dev"`` for
        nodejs or ``"main.toe"`` for TD. Kinds that don't have a concrete
        default can return their display name.
        """
        ...


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# Matches {VAR} or ${VAR} tokens for substituting project variables into
# a run_config's command string before shlex-splitting into argv.
_VAR_REF = re.compile(r"\$?\{([A-Za-z_][A-Za-z0-9_]*)\}")


def substitute_vars(template: str, variables: dict[str, Any]) -> str:
    """Replace {VAR} / ${VAR} tokens in `template` with values from `variables`.

    Unknown tokens are left literal so the command still parses (e.g. shell
    variables the user wants the spawned shell to interpret survive). Values
    are coerced to str.
    """
    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            return match.group(0)
        return str(variables[name])

    return _VAR_REF.sub(repl, template)


def build_argv_from_command(
    command: str | None,
    args: list[str],
    variables: dict[str, Any],
    *,
    shell: bool = False,
) -> list[str]:
    """Build a subprocess argv from a command template + args.

    - Substitute `{VAR}` / `${VAR}` tokens in both `command` and `args`
      using `variables`.
    - If `command` is None/empty, return just the substituted `args`.
    - If `shell=True`, return a single-element list with the full string
      (Popen will hand it to the shell); otherwise shlex-split `command`
      into argv tokens.
    """
    subbed_args = [substitute_vars(a, variables) for a in args]
    if not command:
        return subbed_args
    subbed_cmd = substitute_vars(command, variables)
    if shell:
        full = subbed_cmd
        if subbed_args:
            full = f"{full} {' '.join(shlex.quote(a) for a in subbed_args)}"
        return [full]
    # POSIX-style tokenization by default; Windows can read forward-slash paths fine.
    tokens = shlex.split(subbed_cmd, posix=True)
    return tokens + subbed_args


def env_from_variables(variables: dict[str, Any]) -> dict[str, str]:
    """Coerce every variable value to str, suitable for Popen's env kwarg."""
    return {str(k): str(v) for k, v in variables.items()}


def default_install_steps(_project_folder: Path) -> list[InstallStep]:
    """Empty by default — kinds that don't have a dependency manifest
    (shell scripts, executables, generic projects, TD) return nothing
    so the New Project dialog hides the install checkbox entirely.
    """
    return []


def match_process(
    record: "ProcessRecord", *, strict: bool, default_pattern: str = ""
) -> "psutil.Process | None":
    """The live process `record` refers to, or None.

    Windows (and every OS) reuses PIDs, so a PID alone can point at an
    unrelated program by the time ALPACKA looks again. `record.create_time`
    (stored at launch) pins the exact process: a reused PID has a different
    start time. Rows registered before that was stored fall back to the
    process-name check; with no name pattern either, `strict` (used before
    killing) refuses, because a bare PID proves nothing.
    """
    try:
        proc = psutil.Process(record.pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    created = getattr(record, "create_time", None)
    if created:
        try:
            if abs(proc.create_time() - float(created)) > 1.0:
                return None
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
        return proc
    pattern = (record.process_name_pattern or default_pattern or "").lower()
    if not pattern:
        return None if strict else proc
    try:
        name = (proc.name() or "").lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    return proc if pattern in name else None


def default_liveness(record: "ProcessRecord", default_pattern: str = "") -> bool:
    """Is the process `record` refers to still running? (`match_process`)

    Shared by every kind; TD passes its default name pattern for rows
    that predate the stored pattern.
    """
    return match_process(record, strict=False, default_pattern=default_pattern) is not None


def default_terminate(
    record: "ProcessRecord", timeout: float, default_pattern: str = ""
) -> bool:
    """Graceful terminate then force-kill — only the process ALPACKA started.

    Never touches a PID that now belongs to something else. Returns True
    once ALPACKA's process is gone.
    """
    proc = match_process(record, strict=True, default_pattern=default_pattern)
    if proc is None:
        # Ours is gone, or the PID now belongs to another program (or can't
        # be verified): either way, don't kill anything.
        return not psutil.pid_exists(record.pid)

    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except psutil.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=timeout)
        except (psutil.NoSuchProcess, psutil.TimeoutExpired):
            pass
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    try:
        return not proc.is_running()
    except Exception:  # noqa: BLE001
        return True


def default_focus(record: "ProcessRecord") -> bool:
    """Best-effort foreground raise. Generic across kinds with a window.

    On Windows, looks up the top-level visible titled window owned by the
    pid. Linux uses wmctrl. macOS has no generic activator — kinds that
    need it (e.g. touchdesigner) override this.
    """
    pid = record.pid
    if not psutil.pid_exists(pid):
        return False
    if sys.platform == "win32":
        return _focus_windows_pid(pid)
    if sys.platform == "linux":
        return _focus_linux_pid(pid)
    # macOS — no safe generic activator (we'd need to know the .app bundle).
    return False


def _focus_windows_pid(pid: int) -> bool:
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return False

    user32 = ctypes.windll.user32
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
    )
    matching: list[int] = []

    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetParent(hwnd) != 0:
            return True
        pid_out = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_out))
        if pid_out.value != pid:
            return True
        if user32.GetWindowTextLengthW(hwnd) <= 0:
            return True
        matching.append(int(hwnd))
        return True

    user32.EnumWindows(EnumWindowsProc(_callback), 0)
    if not matching:
        return False

    hwnd = matching[0]
    if user32.IsIconic(hwnd):
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
    try:
        user32.AllowSetForegroundWindow(pid)
    except Exception:  # noqa: BLE001
        pass
    return bool(user32.SetForegroundWindow(hwnd))


def _focus_linux_pid(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["wmctrl", "-lp"], capture_output=True, text=True, check=False, timeout=3
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        parts = line.split(maxsplit=4)
        if len(parts) < 3:
            continue
        try:
            win_pid = int(parts[2])
        except ValueError:
            continue
        if win_pid == pid:
            win_id = parts[0]
            subprocess.run(["wmctrl", "-ia", win_id], check=False, timeout=3)
            return True
    return False
