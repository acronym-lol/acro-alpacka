"""Launch-dependency kind handler.

A "dependency" is a companion process the launcher spawns alongside a
project's main launch — a Redis sidecar, a Python OSC bridge, an
`npm run dev` for a coupled web UI. Dependencies are never the main
target of a launch and are never instantiated as a project on their own.

The handler exists only so `process_tracker`'s kind-dispatch table can
route liveness / focus / terminate calls to the right place. PID
liveness uses the runtime-derived `process_name_pattern` stored at
spawn time (e.g. ``"python"`` for ``python -m worker``) so the OS
PID-reuse defense (docs/process-tracking.md) is preserved.

Project-level methods (`build_launch`, `detect`, `default_run_config`,
etc.) are stubs — raised or no-op — because deps don't drive any of
those flows.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import (
    DetectResult,
    InstallStep,
    LaunchSpec,
    RunConfig,
    default_liveness,
    default_terminate,
)

if TYPE_CHECKING:
    from ..process_tracker import ProcessRecord
    from ..project import Project


class DependencyHandler:
    name = "dependency"
    display_name = "Launch Dependency"
    icon = "terminal"
    long_running = True
    supports_versions = False
    default_process_name_pattern = ""
    default_output_mode = "none"
    produces_install_steps = False
    universal_folders: tuple[str, ...] = ()

    # --- Lifecycle (only methods process_tracker dispatches to) ---

    def liveness(self, record: "ProcessRecord") -> bool:
        return default_liveness(record)

    def focus(self, record: "ProcessRecord") -> bool:
        # Deps generally have no GUI surface; the card UI doesn't expose
        # Focus on dep rows, so this is just a defensive no-op for the
        # rare case process_tracker.focus_window is called with a dep PID.
        return False

    def terminate(self, record: "ProcessRecord", timeout: float) -> bool:
        return default_terminate(record, timeout)

    # --- Stubs for the rest of the protocol ---

    def build_launch(
        self,
        project: "Project",
        run_config: RunConfig,
        merged_vars: dict[str, Any],
        **_extras: Any,
    ) -> LaunchSpec:
        raise RuntimeError(
            "dependency kind has no main launch path — deps are spawned "
            "by launch._spawn_launch_dependencies"
        )

    def default_run_config(self, project: "Project") -> RunConfig:
        return RunConfig(name="default")

    def install_steps(self, project_folder: Path) -> list[InstallStep]:
        return []

    def detect(self, folder: Path) -> DetectResult:
        return DetectResult(confidence=0.0)

    def display_url(
        self, project: "Project", variables: dict[str, Any]
    ) -> str | None:
        return None

    def target_summary(self, project: "Project") -> str:
        return ""


HANDLER = DependencyHandler()
