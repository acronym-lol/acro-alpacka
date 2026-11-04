"""Review a project's run settings before ALPACKA runs them.

A project folder can come from anywhere (a git clone, a shared drive), and
its `alpacka.yml` / `alpacka.local.yml` can make ALPACKA run commands:
run configs, launch dependencies, an executable path, environment
variables, TouchDesigner arguments and editor paths, and auto-launch at
startup. Before ALPACKA runs any of that for the first time on this
machine — and again whenever it changes — the user sees exactly what will
run and approves it.

The approval is a fingerprint (SHA-256) of the project's run settings,
stored per machine in the library DB (`projects.approved_run_hash`).
Fields that change constantly and run nothing (`td_version`, `last_opened`,
`name`, `tags`, …) are left out so they never trigger a new review.

What approval can't do: opening a `.toe` runs that project's Python, and
`npm` / `pip` run a package's scripts. Approval covers what `alpacka.yml`
adds on top, not the project's own code.

Qt-free; the UI subscribes to `subscribe()` to hear about launches that
were held back (auto-launch, crash relaunch).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Callable

from . import kinds, library, project

# Stored by the v6→v7 migration on projects that were already in the
# library; replaced with their current fingerprint at the next startup.
PENDING_UPGRADE = "pending-upgrade"

# kind_data.touchdesigner keys that AcroCore or the library view rewrite
# routinely and that don't change what runs.
_TD_VOLATILE = frozenset({"td_version", "td_major", "default_toes", "alternate_toe_files"})
_TD_LAUNCH_PATHS = ("editor", "table_editor", "web_launch")


def _escapes_folder(rel: str) -> bool:
    """Could `rel` point outside the project folder, on any OS?

    Checked with both Windows and POSIX rules, so a project file written on
    one platform gets the same review on the other.
    """
    for p in (PureWindowsPath(rel), PurePosixPath(rel)):
        if p.is_absolute() or p.drive or p.anchor or ".." in p.parts:
            return True
    return False


def _run_surface(proj: project.Project) -> dict[str, Any]:
    kind_data: dict[str, Any] = {}
    for kind_name, fields in proj.kind_data.items():
        if kind_name == project.TOUCHDESIGNER_KIND:
            td = {k: v for k, v in fields.items() if k not in _TD_VOLATILE}
            toe = str(td.pop("toe_file", "") or "")
            if toe and _escapes_folder(toe):
                td["toe_file"] = toe
            if not td.get("launch_settings"):
                td.pop("launch_settings", None)
            if td:
                kind_data[kind_name] = td
        elif fields:
            kind_data[kind_name] = fields
    return {
        "kind": proj.kind,
        "run_configs": proj.run_configs,
        "launch_dependencies": project.enabled_launch_dependencies(proj),
        "variables": proj.variables,
        "launch_on_startup": bool(proj.launch_on_startup),
        "reopen_on_crash": bool(proj.reopen_on_crash),
        "kind_data": kind_data,
    }


def fingerprint(proj: project.Project) -> str:
    blob = json.dumps(_run_surface(proj), sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def needs_review(proj: project.Project) -> bool:
    """False for a TouchDesigner project that only opens its own `.toe`.

    Every other kind runs a command, and a TD project needs review once its
    `alpacka.yml` adds anything: dependencies, variables, run configs,
    TouchDesigner arguments or editor paths, a `.toe` outside the folder,
    or automatic launching.
    """
    if proj.kind != project.TOUCHDESIGNER_KIND:
        return True
    surface = _run_surface(proj)
    return bool(
        surface["run_configs"]
        or surface["launch_dependencies"]
        or surface["variables"]
        or surface["launch_on_startup"]
        or surface["reopen_on_crash"]
        or surface["kind_data"]
    )


def is_approved(entry: library.LibraryEntry) -> bool:
    proj = entry.project
    if proj is None:
        return True  # nothing to run; launch fails on its own
    if not needs_review(proj):
        return True
    return entry.approved_run_hash == fingerprint(proj)


def approve(entry: library.LibraryEntry) -> None:
    """Record the entry's current run settings as approved."""
    if entry.project is None:
        return
    value = fingerprint(entry.project)
    library.set_approved_run_hash(entry.row_id, value)
    entry.approved_run_hash = value


def approve_row(row_id: int) -> None:
    """Approve a project ALPACKA itself just created or set up."""
    entry = library.get(row_id)
    if entry is not None:
        approve(entry)


def was_approved(row_id: int, proj: project.Project) -> bool:
    """Whether `proj` (loaded just before an edit) is approved as it stands."""
    if not needs_review(proj):
        return True
    entry = library.get(row_id)
    return entry is not None and entry.approved_run_hash == fingerprint(proj)


def carry_forward(row_id: int, approved_before: bool) -> None:
    """After an edit made in ALPACKA, keep an existing approval current.

    If the project was approved just before the edit (`was_approved`), the
    edited settings are approved too: the user just wrote them. If it
    wasn't, it stays that way, because an edit doesn't approve changes
    nobody has reviewed.
    """
    if not approved_before:
        return
    entry = library.get(row_id)
    if entry is not None and entry.project is not None:
        approve(entry)


def grandfather_existing() -> None:
    """Approve projects that were in the library before approval existed.

    The DB migration marks them `PENDING_UPGRADE`; this stores each one's
    current fingerprint, so updating ALPACKA doesn't stop a show machine's
    auto-launch. Later changes to those projects are reviewed as usual.
    """
    for entry in library.list_entries():
        if entry.approved_run_hash != PENDING_UPGRADE:
            continue
        if entry.project is None:
            library.set_approved_run_hash(entry.row_id, None)
        else:
            approve(entry)


# ---------------------------------------------------------------------------
# What runs, in words
# ---------------------------------------------------------------------------


def describe(proj: project.Project) -> str:
    """Plain-text list of everything this project would make ALPACKA run."""
    lines: list[str] = []
    handler = kinds.get(proj.kind)
    lines.append(f"{proj.name or proj.folder.name} ({getattr(handler, 'display_name', proj.kind)})")
    lines.append(f"Folder: {proj.folder}")
    lines.append("")

    lines.append("Runs:")
    if proj.kind == project.TOUCHDESIGNER_KIND:
        lines.append(f"  • Opens {proj.toe_file or '(no .toe set)'} in TouchDesigner")
        if proj.td_extra_args:
            lines.append(f"  • TouchDesigner arguments: {' '.join(proj.td_extra_args)}")
        settings = proj.kind_data.get(project.TOUCHDESIGNER_KIND, {}).get("launch_settings") or {}
        for key in _TD_LAUNCH_PATHS:
            if settings.get(key):
                lines.append(f"  • {key.replace('_', ' ').capitalize()} program: {settings[key]}")
    else:
        configs = proj.run_configs or []
        if not configs:
            default = handler.default_run_config(proj)
            configs = [{
                "name": default.name,
                "command": default.command,
                "args": list(default.args or []),
            }]
        for cfg in configs:
            if not isinstance(cfg, dict):
                continue
            cmd = " ".join(
                str(p) for p in [cfg.get("command") or "", *(cfg.get("args") or [])] if p != ""
            ) or "(the kind's default command)"
            extras = []
            if cfg.get("shell"):
                extras.append("through the system shell")
            if cfg.get("cwd"):
                extras.append(f"in {cfg['cwd']}")
            suffix = f" ({', '.join(extras)})" if extras else ""
            lines.append(f"  • {cfg.get('name') or 'default'}: {cmd}{suffix}")
        settings = proj.kind_data.get(proj.kind) or {}
        for key, value in settings.items():
            lines.append(f"  • {key}: {value}")

    deps = project.enabled_launch_dependencies(proj)
    if deps:
        lines.append("")
        lines.append("Also starts:")
        for dep in deps:
            label = dep.get("name") or "dependency"
            flags = []
            if dep.get("oneshot"):
                flags.append("runs once")
            if dep.get("keep_alive"):
                flags.append("keeps running after the main app exits")
            suffix = f" ({', '.join(flags)})" if flags else ""
            lines.append(f"  • {label}: {dep.get('command')}{suffix}")

    if proj.variables:
        lines.append("")
        lines.append("Environment variables:")
        for name, value in proj.variables.items():
            lines.append(f"  {name}={value}")

    auto = []
    if proj.launch_on_startup:
        auto.append("Starts automatically when ALPACKA opens")
    if proj.launch_on_startup and proj.reopen_on_crash:
        auto.append("Relaunches automatically after a crash or freeze")
    if auto:
        lines.append("")
        lines.append("Automatic:")
        lines.extend(f"  • {a}" for a in auto)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Held-back launches (auto-launch, crash relaunch) → UI banner
# ---------------------------------------------------------------------------

_listeners: list[Callable[[list[str]], None]] = []


def subscribe(callback: Callable[[list[str]], None]) -> None:
    if callback not in _listeners:
        _listeners.append(callback)


def unsubscribe(callback: Callable[[list[str]], None]) -> None:
    if callback in _listeners:
        _listeners.remove(callback)


def notify_held(names: list[str]) -> None:
    """Tell the UI these projects weren't started because they need review."""
    if not names:
        return
    for cb in list(_listeners):
        try:
            cb(list(names))
        except Exception:  # noqa: BLE001 — a UI listener must never break a launch
            pass
