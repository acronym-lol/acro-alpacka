"""Docker Compose kind — runs `docker compose up` on a compose file.

Detects `docker-compose.yml` / `compose.yml` in the project folder.
Variables are forwarded as env vars (compose picks them up for
`${VAR}` interpolation in the compose file).
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


class DockerComposeHandler:
    name = "docker-compose"
    display_name = "Docker Compose"
    icon = "docker"
    long_running = True
    supports_versions = False
    default_process_name_pattern = ""  # compose exits; its containers run separately
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
        kind_data = project.kind_data.get("docker-compose", {})
        compose_file = str(kind_data.get("compose_file", "") or "")
        if not compose_file:
            compose_file = _default_compose_file(project.folder)

        argv = ["docker", "compose"]
        if compose_file:
            argv.extend(["-f", compose_file])

        # Run-config command is optional: "up" (default), "up -d", "run <svc>", etc.
        command = run_config.command or "up"
        command = substitute_vars(command, merged_vars)
        argv.extend(command.split())

        for a in run_config.args:
            argv.append(substitute_vars(a, merged_vars))

        cwd = project.folder
        if run_config.cwd:
            cwd = project.folder / run_config.cwd

        return LaunchSpec(
            argv=argv,
            cwd=cwd,
            env=env_from_variables(merged_vars),
            shell=False,
            process_name_pattern="docker",
            run_config_name=run_config.name,
            artifact_filename=compose_file,
        )

    def default_run_config(self, project: "Project") -> RunConfig:
        return RunConfig(name="default", command="up", long_running=True)

    def liveness(self, record: "ProcessRecord") -> bool:
        return default_liveness(record)

    def focus(self, record: "ProcessRecord") -> bool:
        return default_focus(record)

    def terminate(self, record: "ProcessRecord", timeout: float) -> bool:
        return default_terminate(record, timeout)

    def install_steps(self, project_folder: Path) -> list[InstallStep]:
        # `docker compose up` pulls images at launch time; no separate
        # install step is needed.
        return default_install_steps(project_folder)

    def detect(self, folder: Path) -> DetectResult:
        for candidate in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            if (folder / candidate).is_file():
                return DetectResult(
                    confidence=0.9, signals=[f"found {candidate}"]
                )
        return DetectResult(confidence=0.0)

    def display_url(
        self, project: "Project", variables: dict[str, Any]
    ) -> str | None:
        port = variables.get("PORT")
        if port is None:
            return None
        host = variables.get("HOST") or "localhost"
        if str(host) in ("0.0.0.0", "::"):
            host = "localhost"
        return f"http://{host}:{port}"

    def target_summary(self, project: "Project") -> str:
        return "$ docker compose up"


def _default_compose_file(folder: Path) -> str:
    for candidate in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        if (folder / candidate).is_file():
            return candidate
    return "docker-compose.yml"


HANDLER = DockerComposeHandler()
