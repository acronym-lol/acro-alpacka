"""Shell kind — runs a shell script (.sh/.bat/.ps1) or a raw shell command.

On Windows, .bat and .ps1 files spawn via cmd / PowerShell respectively.
On POSIX, .sh files invoke the shebang or default to `sh`.
"""
from __future__ import annotations

import sys
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


class ShellHandler:
    name = "shell"
    display_name = "Shell Script"
    icon = "terminal"
    long_running = False
    supports_versions = False
    default_process_name_pattern = ""
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
            shell=True,  # shell kind always goes through the shell
        )
        if not argv:
            raise RuntimeError(
                f"shell kind: run_config '{run_config.name}' has no command"
            )
        cwd = project.folder
        if run_config.cwd:
            cwd = project.folder / run_config.cwd
        return LaunchSpec(
            argv=argv,
            cwd=cwd,
            env=env_from_variables(merged_vars),
            shell=True,
            process_name_pattern="",
            run_config_name=run_config.name,
            artifact_filename="",
        )

    def default_run_config(self, project: "Project") -> RunConfig:
        # Default to running the canonical entry script if one exists.
        if sys.platform == "win32":
            for candidate in ("run.bat", "start.bat"):
                if (project.folder / candidate).is_file():
                    return RunConfig(name="default", command=candidate, shell=True)
        else:
            for candidate in ("run.sh", "start.sh"):
                if (project.folder / candidate).is_file():
                    return RunConfig(
                        name="default", command=f"./{candidate}", shell=True
                    )
        return RunConfig(name="default", command=None, shell=True)

    def liveness(self, record: "ProcessRecord") -> bool:
        return default_liveness(record)

    def focus(self, record: "ProcessRecord") -> bool:
        return default_focus(record)

    def terminate(self, record: "ProcessRecord", timeout: float) -> bool:
        return default_terminate(record, timeout)

    def install_steps(self, project_folder: Path) -> list[InstallStep]:
        # Shell scripts have no dependency manifest — nothing to install.
        return default_install_steps(project_folder)

    def detect(self, folder: Path) -> DetectResult:
        signals: list[str] = []
        score = 0.0
        for candidate in ("run.sh", "start.sh", "run.bat", "start.bat"):
            if (folder / candidate).is_file():
                score = max(score, 0.5)
                signals.append(f"found {candidate}")
        if (folder / "Makefile").is_file():
            score = max(score, 0.4)
            signals.append("found Makefile")
        return DetectResult(confidence=score, signals=signals)

    def display_url(
        self, project: "Project", variables: dict[str, Any]
    ) -> str | None:
        return None

    def target_summary(self, project: "Project") -> str:
        default = self.default_run_config(project)
        if default.command:
            return f"$ {default.command}"
        return "(no shell command configured)"


HANDLER = ShellHandler()
