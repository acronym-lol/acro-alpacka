"""Generic kind — fallback for projects that don't fit any specific mold.

Runs whatever `run_config.command` the user declares in alpacka.yml,
substituting {VAR} / ${VAR} tokens from project variables.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import (
    DetectResult,
    InstallStep,
    LaunchSpec,
    RunConfig,
    build_argv_from_command,
    default_focus,
    default_install_steps,
    default_liveness,
    default_terminate,
    env_from_variables,
)

if TYPE_CHECKING:
    from ..process_tracker import ProcessRecord
    from ..project import Project


class GenericHandler:
    name = "generic"
    display_name = "Generic / Custom"
    icon = "box"
    long_running = False
    supports_versions = False
    default_process_name_pattern = ""  # pid-only liveness
    default_output_mode = "console"
    produces_install_steps = False
    universal_folders: tuple[str, ...] = ()

    def build_launch(
        self,
        project: "Project",
        run_config: RunConfig,
        merged_vars: dict[str, Any],
        **_extras: Any,
    ) -> LaunchSpec:
        argv = build_argv_from_command(
            run_config.command,
            run_config.args,
            merged_vars,
            shell=run_config.shell,
        )
        if not argv:
            raise RuntimeError(
                f"generic kind: run_config '{run_config.name}' has no command"
            )
        cwd = project.folder
        if run_config.cwd:
            cwd = project.folder / run_config.cwd
        return LaunchSpec(
            argv=argv,
            cwd=cwd,
            env=env_from_variables(merged_vars),
            shell=bool(run_config.shell),
            process_name_pattern="",
            run_config_name=run_config.name,
            artifact_filename="",
        )

    def default_run_config(self, project: "Project") -> RunConfig:
        return RunConfig(name="default", command=None, long_running=False)

    def liveness(self, record: "ProcessRecord") -> bool:
        return default_liveness(record)

    def focus(self, record: "ProcessRecord") -> bool:
        return default_focus(record)

    def terminate(self, record: "ProcessRecord", timeout: float) -> bool:
        return default_terminate(record, timeout)

    def install_steps(self, project_folder: Path) -> list[InstallStep]:
        # Generic projects have no convention to detect — leave install
        # to the user.
        return default_install_steps(project_folder)

    def detect(self, folder: Path) -> DetectResult:
        # Fallback only — picked if nothing else matches.
        return DetectResult(confidence=0.05, signals=["fallback"])

    def display_url(
        self, project: "Project", variables: dict[str, Any]
    ) -> str | None:
        return None

    def target_summary(self, project: "Project") -> str:
        for raw in project.run_configs:
            if raw.get("name") in (None, "", "default") and raw.get("command"):
                return f"$ {raw['command']}"
        return "(no command configured)"


HANDLER = GenericHandler()
