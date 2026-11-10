"""Launch a project via its kind handler.

For TouchDesigner projects, the existing `launch` / `launch_with` /
`launch_toe` entry points remain: they resolve the pinned install and
the primary/alternate `.toe` file, then delegate to the TD kind handler
through the kind registry.

For other kinds (added later), callers use `launch(entry)` generically;
the kind handler's `build_launch()` owns argv / cwd / env / shell
construction.

Process registration writes `kind`, `process_name_pattern`, and
`run_config_name` into `running_processes` so the sweep can dispatch
liveness checks per kind.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import psutil

from . import (
    approval,
    kinds,
    launch_runner,
    library,
    output_manager,
    process_tracker,
    project,
    td_install,
    td_prefs,
)
from .kinds.base import substitute_vars
from .settings import TDInstall


class LaunchError(RuntimeError):
    """Generic launch failure (missing .toe, no installs at all, etc.)."""


class NeedsApproval(LaunchError):
    """The project's run settings haven't been reviewed on this machine.

    Raised by `_spawn` for every launch path. The library view checks
    approval before launching and shows `approval.describe()`; auto-launch
    and crash relaunch skip the project and tell the UI instead.
    """

    def __init__(self, entry: library.LibraryEntry) -> None:
        name = (entry.project.name if entry.project else "") or entry.path.name
        super().__init__(
            f"{name} hasn't been reviewed on this machine yet. "
            "Click Launch in the library to see what it runs and approve it."
        )
        self.entry = entry


@dataclass
class VersionMismatch(Exception):
    """Raised when the pinned TD version isn't installed.

    `closest_newer` is the newest installed version >= pinned (or None if
    none installed meets that bar). The caller can offer a "launch with
    newer" affordance.
    """

    pinned: str
    closest_newer: TDInstall | None
    all_installs: list[TDInstall]

    def __str__(self) -> str:
        if self.closest_newer:
            return (
                f"TD {self.pinned} is not installed (closest: {self.closest_newer.version})"
            )
        return f"TD {self.pinned} is not installed"


def launch(
    entry: library.LibraryEntry,
    *,
    run_config_name: str | None = None,
) -> subprocess.Popen:
    """Launch a library entry. Raises LaunchError or VersionMismatch.

    For TouchDesigner projects, resolves the pinned install and launches
    the primary .toe. For other kinds, dispatches through the kind
    handler using `run_config_name` (or the handler's default config).

    On success, updates last_launched + last_opened before returning.
    """
    if entry.project is None:
        raise LaunchError(
            f"alpacka.yml missing or unreadable at {entry.path}"
        )

    if entry.project.kind != kinds.default_kind():
        # Non-TD: dispatch through the kind registry. Pick the run config
        # by name (from alpacka.yml's `run_configs:`) or fall back to the
        # handler's default.
        run_config = _resolve_run_config(entry.project, run_config_name)
        return _spawn(entry, run_config=run_config)

    toe_path = entry.project.toe_path
    if not toe_path or not toe_path.is_file():
        raise LaunchError(f".toe file missing: {toe_path}")

    install = _resolve_td_install(entry.project)
    return _spawn(entry, install=install, toe_path=toe_path)


def _resolve_run_config(
    proj: "project.Project", name: str | None
) -> kinds.RunConfig:
    """Look up a named run_config in alpacka.yml, fall back to kind default."""
    handler = kinds.get(proj.kind)
    if name:
        for raw in proj.run_configs:
            if raw.get("name") == name:
                return _run_config_from_dict(raw)
    # No name given, or not found — use the kind's default. If alpacka.yml
    # happens to declare a "default" run_config, honor its fields.
    for raw in proj.run_configs:
        if raw.get("name") in (None, "", "default"):
            return _run_config_from_dict(raw)
    return handler.default_run_config(proj)


def _run_config_from_dict(raw: dict) -> kinds.RunConfig:
    return kinds.RunConfig(
        name=str(raw.get("name") or "default"),
        command=raw.get("command"),
        args=list(raw.get("args") or []),
        cwd=raw.get("cwd"),
        shell=bool(raw.get("shell", False)),
        variables=dict(raw.get("variables") or {}),
        env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
        long_running=raw.get("long_running"),
        terminate_signal=str(raw.get("terminate_signal") or "TERM"),
    )


def launch_with(
    entry: library.LibraryEntry, install: TDInstall
) -> subprocess.Popen:
    """Launch using an explicitly chosen TD install. No version checks."""
    if entry.project is None:
        raise LaunchError(
            f"alpacka.yml missing or unreadable at {entry.path}"
        )
    toe_path = entry.project.toe_path
    if not toe_path or not toe_path.is_file():
        raise LaunchError(f".toe file missing: {toe_path}")
    return _spawn(entry, install=install, toe_path=toe_path)


def launch_toe(
    entry: library.LibraryEntry,
    toe_filename: str,
    *,
    install: TDInstall | None = None,
) -> subprocess.Popen:
    """Launch a specific .toe (primary or alternate) from a library entry.

    `toe_filename` is the filename relative to the project folder (matches
    `project.toe_file` for the primary, or a value from
    `project.alternate_toe_files`). If `install` is None, the project's
    pinned version is used (raising VersionMismatch if unavailable).
    """
    if entry.project is None:
        raise LaunchError(
            f"alpacka.yml missing or unreadable at {entry.path}"
        )
    toe_path = entry.project.resolve_toe(toe_filename)
    if toe_path is None or not toe_path.is_file():
        raise LaunchError(f".toe file missing: {toe_path}")

    if install is None:
        install = _resolve_td_install(entry.project)
    return _spawn(entry, install=install, toe_path=toe_path)


def _resolve_td_install(proj: "project.Project") -> TDInstall:
    """Pick the TD install that matches the project's pinned version.

    Raises `LaunchError` if no TD is installed, `VersionMismatch` if the
    pinned version is missing. Unpinned projects use the newest install.
    """
    installs = td_install.detect()
    if not installs:
        raise LaunchError(
            "No TouchDesigner installation detected. Install TD to launch projects."
        )
    pinned = proj.td_version
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


def _spawn(
    entry: library.LibraryEntry,
    *,
    install: TDInstall | None = None,
    toe_path: Path | None = None,
    run_config: kinds.RunConfig | None = None,
) -> subprocess.Popen:
    """Dispatch launch through the kind registry and register the PID.

    `install` / `toe_path` are TD-specific overrides consumed by the
    TouchDesigner handler's `build_launch(**extras)`. Other kinds ignore
    them.
    """
    proj = entry.project
    if proj is None:
        raise LaunchError(
            f"alpacka.yml missing or unreadable at {entry.path}"
        )
    # Single choke point for every launch path (library, auto-launch,
    # launch_with, crash relaunch): nothing from alpacka.yml runs until the
    # user has approved the project's current run settings.
    if not approval.is_approved(entry):
        raise NeedsApproval(entry)

    handler = kinds.get(proj.kind)
    if run_config is None:
        run_config = handler.default_run_config(proj)

    merged_vars = dict(proj.variables)
    merged_vars.update(run_config.variables)

    extras: dict = {}
    if install is not None:
        extras["install"] = install
        # Every TD launch funnels through here (library view, auto-launch,
        # launch_with, crash relaunch), so this is where the managed
        # pref.txt keys get applied — just before TD reads the file.
        _sync_td_prefs(install)
    if toe_path is not None:
        extras["artifact"] = toe_path

    spec = handler.build_launch(proj, run_config, merged_vars, **extras)

    env = dict(os.environ)
    for k, v in spec.env.items():
        env[str(k)] = str(v)

    # Resolve the project's effective output mode, then dispatch to the
    # matching runner. TD projects always go through `silent` — the TD
    # process owns its own window + log, so capturing stdio would buy
    # nothing and could break TD's own console/log integration.
    if proj.kind == kinds.default_kind():
        output_mode = "none"
    else:
        output_mode = proj.effective_output_mode(handler) or "none"

    # A console window normally waits for a key after an error so the
    # output stays readable. With Auto Relaunch on, nobody may be there to
    # press it, and the waiting window would hide the crash.
    relaunches = bool(proj.launch_on_startup and proj.reopen_on_crash)
    proc, output_mode = _dispatch_runner(
        spec, env, output_mode, pause_on_error=not relaunches
    )

    # Register the spawned PID so the library view can surface running
    # state and later focus / terminate the process. Defensive — DB failure
    # shouldn't block the launch.
    try:
        process_tracker.register(
            entry.row_id,
            proc.pid,
            kind=handler.name,
            process_name_pattern=spec.process_name_pattern,
            run_config_name=spec.run_config_name,
            toe_file=spec.artifact_filename,
            td_version=install.version if install is not None else "",
            output_mode=output_mode,
        )
    except Exception:  # noqa: BLE001
        pass

    # Inline mode: hand the proc to output_manager so the capture
    # worker streams stdout into the per-project ring buffer. Done
    # AFTER process_tracker.register so the dialog can correlate the
    # buffer with the row id immediately.
    if output_mode == "inline":
        try:
            output_manager.attach(entry.row_id, proc)
        except Exception:  # noqa: BLE001
            # Capture wiring failure shouldn't kill the launch — the
            # process is already running; we just lose live tailing.
            pass

    # One-shot runs need a watcher thread to capture their exit code.
    # Long-running kinds rely on the periodic sweep for liveness instead.
    is_long_running = (
        run_config.long_running
        if run_config.long_running is not None
        else handler.long_running
    )
    if not is_long_running:
        threading.Thread(
            target=_watch_completion,
            args=(proc,),
            daemon=True,
            name=f"alpacka-watcher-{proc.pid}",
        ).start()

    library.mark_launched(entry.row_id)
    if entry.project is not None:
        project.touch_last_opened(entry.project)

    # Spawn user-declared launch dependencies alongside the main process.
    # Done AFTER mark_launched / touch_last_opened so a dep failure can't
    # poison the main launch's bookkeeping. Failures are logged to stderr
    # and surfaced in Edit Details (each row's status pill shows
    # "not spawned" if the dep didn't come up).
    try:
        _spawn_launch_dependencies(entry, proj, merged_vars, output_mode)
    except Exception:  # noqa: BLE001 — defensive: dep wiring shouldn't kill the launch
        pass

    return proc


def _sync_td_prefs(install: TDInstall) -> None:
    """Apply the user's managed TD preferences. Never blocks the launch.

    `td_prefs.reconcile` already swallows its own errors into a result;
    the extra guard covers the process scan.
    """
    try:
        td_prefs.reconcile(
            install.major,
            td_running=process_tracker.any_td_running(),
            source="launch",
        )
    except Exception:  # noqa: BLE001
        pass


def _spawn_launch_dependencies(
    entry: library.LibraryEntry,
    proj: "project.Project",
    merged_vars: dict,
    project_output_mode: str,
) -> None:
    """Spawn each enabled launch_dependency for the project.

    Per dep:
    - shlex-split the command (after {VAR} substitution) into argv
    - spawn via the same runner the project's output_mode dictates
    - register in process_tracker with kind="dependency",
      run_config_name="dep:<index>", toe_file=<command text>
    - register process_name_pattern from psutil so the PID-reuse name-check
      (docs/process-tracking.md) survives — falls back to argv[0] basename if psutil
      can't read the process name yet
    - if oneshot, spawn the existing _watch_completion daemon thread so
      mark_completed records the exit code
    """
    deps = project.enabled_launch_dependencies(proj)
    if not deps:
        return

    # Same env layering main launches use: project variables + run_config
    # variables, merged onto os.environ. Copy of os.environ first so
    # other variable substitutions don't leak into the parent process.
    base_env = dict(os.environ)
    for k, v in merged_vars.items():
        base_env[str(k)] = str(v)

    for index, dep in enumerate(deps):
        try:
            _spawn_one_launch_dependency(
                entry, proj, dep, index, merged_vars, base_env, project_output_mode
            )
        except Exception as exc:  # noqa: BLE001
            label = str(dep.get("name") or dep.get("command") or f"dep:{index}")
            print(
                f"[alpacka] Failed to start launch dependency '{label}': {exc}",
                file=sys.stderr,
            )


def _spawn_one_launch_dependency(
    entry: library.LibraryEntry,
    proj: "project.Project",
    dep: dict,
    index: int,
    merged_vars: dict,
    base_env: dict,
    project_output_mode: str,
) -> None:
    raw_command = str(dep.get("command", "") or "").strip()
    if not raw_command:
        return
    command = substitute_vars(raw_command, merged_vars)
    argv = shlex.split(command, posix=True)
    if not argv:
        return

    cwd = entry.path
    shell = False
    label = str(dep.get("name") or "").strip() or f"dep:{index}"
    oneshot = bool(dep.get("oneshot"))
    keep_alive = bool(dep.get("keep_alive"))

    # Console mode for a dep means a separate terminal window per dep —
    # noisy, but consistent with the user's chosen output_mode. Inline
    # streams into the project's shared OutputBuffer with a [<label>]
    # prefix on each line so deps and main are distinguishable. Silent
    # is the no-op default.
    if project_output_mode == "console":
        try:
            proc = launch_runner.run_console(
                argv, cwd=cwd, env=base_env, shell=shell
            )
            effective_mode = "console"
        except launch_runner.ConsoleUnavailableError:
            proc = launch_runner.run_inline(
                argv, cwd=cwd, env=base_env, shell=shell
            )
            effective_mode = "inline"
            output_manager.attach(entry.row_id, proc, source=label)
    elif project_output_mode == "inline":
        proc = launch_runner.run_inline(argv, cwd=cwd, env=base_env, shell=shell)
        effective_mode = "inline"
        try:
            output_manager.attach(entry.row_id, proc, source=label)
        except Exception:  # noqa: BLE001
            pass
    else:
        proc = launch_runner.run_silent(argv, cwd=cwd, env=base_env, shell=shell)
        effective_mode = "none"

    # Derive process_name_pattern from the live process so the liveness
    # name-check (docs/process-tracking.md) defends against OS PID reuse. Falls back
    # to argv[0] basename if psutil can't read the name yet (e.g. the
    # child is still bootstrapping).
    pattern = _derive_process_name_pattern(proc.pid, argv)

    try:
        process_tracker.register(
            entry.row_id,
            proc.pid,
            kind="dependency",
            process_name_pattern=pattern,
            run_config_name=f"dep:{index}",
            toe_file=command,
            output_mode=effective_mode,
            keep_alive_after_main=keep_alive,
            oneshot=oneshot,
        )
    except Exception:  # noqa: BLE001
        pass

    if oneshot:
        threading.Thread(
            target=_watch_completion,
            args=(proc,),
            daemon=True,
            name=f"alpacka-dep-watcher-{proc.pid}",
        ).start()


def _derive_process_name_pattern(pid: int, argv: list[str]) -> str:
    """Pattern for the PID-reuse defense in default_liveness.

    Prefer psutil's live-process name (handles wrappers like
    ``python -m foo`` whose argv[0] is `python` matching the real exe).
    Fall back to ``argv[0]`` basename when psutil can't read the name.
    Empty string disables the name-check entirely (pid-only liveness)
    rather than asserting a wrong pattern.
    """
    try:
        name = psutil.Process(pid).name() or ""
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        name = ""
    if name:
        cleaned = name.lower()
        if cleaned.endswith(".exe"):
            cleaned = cleaned[:-4]
        return cleaned
    if argv:
        stem = Path(argv[0]).stem.lower()
        if stem:
            return stem
    return ""


def _dispatch_runner(
    spec: kinds.LaunchSpec,
    env: dict[str, str],
    output_mode: str,
    *,
    pause_on_error: bool = True,
) -> tuple[subprocess.Popen, str]:
    """Pick a runner based on `output_mode`; return (proc, effective_mode).

    Falls back from console → inline if the platform can't open a
    terminal (currently only fires on Linux when the fallback chain
    yields nothing). The effective mode is returned so the caller
    persists what actually happened, not what was requested.
    `pause_on_error` only affects console mode.
    """
    if output_mode == "console":
        try:
            proc = launch_runner.run_console(
                spec.argv,
                cwd=spec.cwd,
                env=env,
                shell=spec.shell,
                pause_on_error=pause_on_error,
            )
            return proc, "console"
        except launch_runner.ConsoleUnavailableError:
            # Fall through to inline. The user will discover the View
            # Output card button; documented contract.
            output_mode = "inline"

    if output_mode == "inline":
        proc = launch_runner.run_inline(
            spec.argv, cwd=spec.cwd, env=env, shell=spec.shell
        )
        return proc, "inline"

    proc = launch_runner.run_silent(
        spec.argv, cwd=spec.cwd, env=env, shell=spec.shell
    )
    return proc, "none"


def _watch_completion(proc: subprocess.Popen) -> None:
    """Block until `proc` exits, then mark the DB row completed.

    Runs on a daemon thread so it doesn't hold the launcher open. The row
    stays in `running_processes` with `exit_code` + `completed_at` set so
    the UI can show the result; it no longer counts as running, and
    `process_tracker.clear_completed()` drops it at the next startup. For inline
    captures this also flips the OutputBuffer's status to `("exited", N)`
    so any open OutputView dialog can render the final state.
    """
    try:
        exit_code = proc.wait()
    except Exception:  # noqa: BLE001
        exit_code = None
    try:
        record = process_tracker.get_by_pid(proc.pid)
    except Exception:  # noqa: BLE001
        record = None
    try:
        process_tracker.mark_completed(proc.pid, exit_code)
    except Exception:  # noqa: BLE001
        pass
    if record is not None and record.output_mode == "inline":
        try:
            output_manager.notify_exit(record.project_id, exit_code or 0)
        except Exception:  # noqa: BLE001
            pass
