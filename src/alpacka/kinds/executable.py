"""Executable kind — launches a single binary (.exe/.bat/.ps1) or shell script.

Users point at a concrete executable via `kind_data.executable.path`
(relative or absolute). Args and env come from the run config + variables.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import (
    DetectResult,
    InstallStep,
    LaunchSpec,
    RunConfig,
    default_focus,
    default_install_steps,
    default_liveness,
    default_terminate,
    env_from_variables,
    substitute_vars,
)

if TYPE_CHECKING:
    from ..process_tracker import ProcessRecord
    from ..project import Project


class ExecutableHandler:
    name = "executable"
    display_name = "Executable"
    icon = "app-window"
    long_running = False
    supports_versions = False
    default_process_name_pattern = ""
    default_output_mode = "none"  # GUI apps own their own window; user can flip per project
    produces_install_steps = False
    universal_folders: tuple[str, ...] = ()

    def build_launch(
        self,
        project: "Project",
        run_config: RunConfig,
        merged_vars: dict[str, Any],
        **_extras: Any,
    ) -> LaunchSpec:
        kind_data = project.kind_data.get("executable", {})
        exe_path_str = str(kind_data.get("path", "") or "")
        if not exe_path_str:
            raise RuntimeError(
                "executable kind: kind_data.executable.path is empty — "
                "set it to the executable you want to launch"
            )
        exe_path = Path(substitute_vars(exe_path_str, merged_vars))
        if not exe_path.is_absolute():
            exe_path = project.folder / exe_path

        args = [substitute_vars(a, merged_vars) for a in run_config.args]
        cwd = project.folder
        if run_config.cwd:
            cwd = project.folder / run_config.cwd

        return LaunchSpec(
            argv=[str(exe_path), *args],
            cwd=cwd,
            env=env_from_variables(merged_vars),
            shell=False,
            process_name_pattern=exe_path.stem.lower(),
            run_config_name=run_config.name,
            artifact_filename=exe_path.name,
        )

    def default_run_config(self, project: "Project") -> RunConfig:
        return RunConfig(name="default", long_running=False)

    def liveness(self, record: "ProcessRecord") -> bool:
        return default_liveness(record)

    def focus(self, record: "ProcessRecord") -> bool:
        return default_focus(record)

    def terminate(self, record: "ProcessRecord", timeout: float) -> bool:
        return default_terminate(record, timeout)

    def install_steps(self, project_folder: Path) -> list[InstallStep]:
        # Pre-built binaries don't need installation.
        return default_install_steps(project_folder)

    def detect(self, folder: Path) -> DetectResult:
        # Explicit opt-in — detection never picks this kind automatically.
        return DetectResult(confidence=0.0)

    def display_url(
        self, project: "Project", variables: dict[str, Any]
    ) -> str | None:
        return None

    def target_summary(self, project: "Project") -> str:
        kind_data = project.kind_data.get("executable", {})
        path = str(kind_data.get("path", "") or "")
        if path:
            return path
        return "(executable path not set)"


HANDLER = ExecutableHandler()
