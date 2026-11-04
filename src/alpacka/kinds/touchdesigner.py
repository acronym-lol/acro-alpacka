"""TouchDesigner kind handler.

Wraps the existing TD-specific launch + process-tracking logic
(`td_install.launch_command`, the `"touchdesigner"` process-name check,
Windows `IsIconic`-guarded SW_RESTORE focus, macOS direct Mach-O spawn,
AppleScript fallback) behind the `KindHandler` protocol. Behavior here
must remain byte-identical to the pre-refactor code paths.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psutil

from .. import td_install
from .base import (
    DetectResult,
    InstallStep,
    LaunchSpec,
    RunConfig,
    default_install_steps,
    default_liveness,
    default_terminate,
    env_from_variables,
)

if TYPE_CHECKING:
    from ..process_tracker import ProcessRecord
    from ..project import Project
    from ..settings import TDInstall


NAME = "touchdesigner"


class TouchDesignerHandler:
    name = NAME
    display_name = "TouchDesigner"
    icon = "touchdesigner"
    long_running = True
    supports_versions = True
    # The substring check that guards against OS PID reuse (see docs/process-tracking.md).
    default_process_name_pattern = "touchdesigner"
    # TD owns its own window + log; the launcher's output_mode UI is
    # hidden for TD projects so this default is mostly cosmetic.
    default_output_mode = "none"
    # Python deps for TD projects are managed by the tdPyEnvManager
    # palette component — its `autoSetup` flag creates the venv and
    # installs requirements on first .toe open. The launcher just
    # ships `TDPyEnvManagerContext.yaml` + `requirements.txt` via the
    # template; no install steps run from this side.
    produces_install_steps = False
    # Universal bootstrap folder list previously hardcoded in
    # universal_bootstrap.apply(). Moved here so other kinds don't inherit it.
    universal_folders = ("scripts", "content", "tox", "TDLogs")

    # --- Launch ---

    def build_launch(
        self,
        project: "Project",
        run_config: RunConfig,
        merged_vars: dict[str, Any],
        *,
        install: "TDInstall | None" = None,
        artifact: Path | None = None,
    ) -> LaunchSpec:
        """Open the project's primary `.toe` with the pinned TD install.

        Optional kwargs let `launch_with(entry, install)` and
        `launch_toe(entry, toe_filename, install=...)` pass their
        pre-resolved install / toe path so we don't re-resolve.

        Argv is `[td.exe, *extra_args, toe_path]` — extra_args from
        `kind_data.touchdesigner.launch_settings.extra_args` go between
        the executable and the .toe path (TD's documented arg order).

        Env layering, lowest-priority first:
        1. ``merged_vars`` (project + run_config `variables`) — historical
           back-door for DAT scripts to read project vars from os.environ.
        2. Structured ``launch_settings`` → ``TOUCH_*`` keys.
        3. ``run_config.env`` — explicit override, wins over the rest.
        """
        if artifact is None:
            artifact = project.toe_path
        if artifact is None:
            raise RuntimeError(f"project has no toe_file: {project.folder}")
        if install is None:
            install = _resolve_install(project)
        argv = td_install.launch_command(install, artifact)
        extras = _touch_argv_extras(project)
        if extras:
            argv = [argv[0], *extras, *argv[1:]]
        env = env_from_variables(merged_vars)
        env.update(_touch_env_from_settings(project))
        env.update(env_from_variables(run_config.env))
        return LaunchSpec(
            argv=argv,
            cwd=project.folder,
            env=env,
            shell=False,
            process_name_pattern=self.default_process_name_pattern,
            run_config_name=run_config.name,
            artifact_filename=artifact.name,
        )

    def default_run_config(self, project: "Project") -> RunConfig:
        return RunConfig(name="default", long_running=True)

    # --- Liveness / focus / terminate ---

    def liveness(self, record: "ProcessRecord") -> bool:
        """True iff the process ALPACKA started for `record` is still running.

        Defense against OS PID reuse (docs/process-tracking.md): the stored
        process start time must match, or for older rows the process name
        must contain `record.process_name_pattern` — falling back to the
        class attribute for pre-v3 rows that didn't store it.
        """
        return default_liveness(record, self.default_process_name_pattern)

    def focus(self, record: "ProcessRecord") -> bool:
        pid = record.pid
        if not self.liveness(record):
            return False
        if sys.platform == "win32":
            return _focus_windows(pid)
        if sys.platform == "darwin":
            return _focus_macos(pid)
        return _focus_linux(pid)

    def terminate(self, record: "ProcessRecord", timeout: float) -> bool:
        """Graceful terminate then force-kill; generic across TD platforms.

        Only ever kills the process ALPACKA started (see `liveness`).
        """
        return default_terminate(record, timeout, self.default_process_name_pattern)

    def install_steps(self, project_folder: Path) -> list[InstallStep]:
        # Venv setup is delegated to tdPyEnvManager's autoSetup, which
        # runs inside TD on first open. See `produces_install_steps`.
        return default_install_steps(project_folder)

    # --- Detection + display URL ---

    def detect(self, folder: Path) -> DetectResult:
        signals: list[str] = []
        score = 0.0
        # An explicit `kind: touchdesigner` in alpacka.yml bypasses detection
        # upstream; this method is only consulted when kind is unset / unknown.
        if list(folder.glob("*.toe")):
            score = max(score, 0.95)
            signals.append("found .toe file in folder")
        info_json = folder / "project-info.json"
        if info_json.is_file():
            # Legacy TD project (pre-alpacka.yml). The dev has re-adds on deck
            # per the migration plan; detection still flags this for them.
            score = max(score, 0.9)
            signals.append("found legacy project-info.json")
        return DetectResult(confidence=score, signals=signals)

    def display_url(
        self, project: "Project", variables: dict[str, Any]
    ) -> str | None:
        # TD projects don't expose a URL.
        return None

    def target_summary(self, project: "Project") -> str:
        toes = project.all_toe_filenames()
        if not toes:
            return "(no .toe)"
        if len(toes) == 1:
            return toes[0]
        return f"{toes[0]} + {len(toes) - 1} more"


def _touch_env_from_settings(project: "Project") -> dict[str, str]:
    """Translate `kind_data.touchdesigner.launch_settings` to TOUCH_* env vars.

    General rule: omit any default-state field so the spawned env stays
    clean of inert keys. Specifics:

    - Booleans emit ``"1"`` when on, omitted when off.
    - Strings emit verbatim when non-empty (browser/editor paths,
      space-separated MIDI override, log level).
    - ``help_delay`` is Optional[int] — ``0`` is a documented value
      meaning "disable pop-up help", so we emit whenever it is set,
      including ``0``. Only ``None`` (unset) is omitted.
    """
    env: dict[str, str] = {}
    if project.td_text_console:
        env["TOUCH_TEXT_CONSOLE"] = "1"
    if project.td_disable_update_check:
        env["TOUCH_NO_UPDATE_CHECK"] = "1"
    if project.td_app_log_level:
        env["TOUCH_APP_LOG_LEVEL"] = project.td_app_log_level
    if project.td_quick_crash:
        env["TOUCH_QUICK_CRASH"] = "1"
    if project.td_disable_crash_handlers:
        env["TOUCH_DISABLE_CRASH_HANDLERS"] = "1"
    if project.td_ten_bit_color:
        env["TOUCH_10_BIT_COLOR"] = "1"
    if project.td_double_draw:
        env["TOUCH_DOUBLE_DRAW"] = "1"
    if project.td_editor:
        env["TOUCH_EDITOR"] = project.td_editor
    if project.td_table_editor:
        env["TOUCH_TABLE_EDITOR"] = project.td_table_editor
    if project.td_web_launch:
        env["TOUCH_WEB_LAUNCH"] = project.td_web_launch
    # help_delay is Optional — `0` is a valid "disable pop-up help" value,
    # distinct from "unset", so we forward whenever the setting is present.
    if project.td_help_delay is not None:
        env["TOUCH_HELP_DELAY"] = str(project.td_help_delay)
    if project.td_midi_chan_override:
        env["TOUCH_MIDI_CHAN_OVERRIDE"] = project.td_midi_chan_override
    return env


def _touch_argv_extras(project: "Project") -> list[str]:
    """Raw CLI args injected between td.exe and the .toe path.

    Currently sourced from `launch_settings.extra_args`; common use is
    multi-GPU affinity flags (see TD's "Using Multiple Graphic Cards" docs).
    """
    return list(project.td_extra_args)


def _resolve_install(project: "Project"):
    """Locate the TD install matching the project's pinned version.

    Raises RuntimeError if nothing is installed; raises td_install's
    VersionMismatch (propagated via launch.py) if the pinned version is
    missing. Unpinned projects use the newest install.
    """
    from ..launch import VersionMismatch

    installs = td_install.detect()
    if not installs:
        raise RuntimeError(
            "No TouchDesigner installation detected. Install TD to launch projects."
        )
    pinned = project.td_version
    if not pinned:
        return installs[0]
    found = td_install.find_by_version(installs, pinned)
    if found is not None:
        return found
    raise VersionMismatch(
        pinned=pinned,
        closest_newer=td_install.newest_at_least(installs, pinned),
        all_installs=installs,
    )


# ---------------------------------------------------------------------------
# Platform focus helpers (preserved byte-identical from process_tracker.py).
# SW_RESTORE guard on Windows: only un-minimize when actually minimized;
# calling ShowWindow(hwnd, SW_RESTORE) on a non-minimized window can re-apply
# its remembered "maximized" state. See docs/process-tracking.md#focus.
# ---------------------------------------------------------------------------


def _focus_windows(pid: int) -> bool:
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


def _focus_macos(pid: int) -> bool:
    try:
        app_path = _macos_app_for_pid(pid)
    except Exception:  # noqa: BLE001
        app_path = None

    try:
        if app_path:
            subprocess.run(["open", "-a", app_path], check=False, timeout=5)
            return True
        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "TouchDesigner" to activate',
            ],
            check=False,
            timeout=5,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _focus_linux(pid: int) -> bool:
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
            subprocess.run(
                ["wmctrl", "-ia", win_id], check=False, timeout=3
            )
            return True
    return False


def _macos_app_for_pid(pid: int) -> str | None:
    try:
        exe = psutil.Process(pid).exe()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    if not exe:
        return None
    p = Path(exe)
    for ancestor in p.parents:
        if ancestor.suffix == ".app":
            return str(ancestor)
    return None


# Singleton handler instance. Registered by kinds/__init__.py at package load.
HANDLER = TouchDesignerHandler()
