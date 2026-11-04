"""Node.js kind — runs `npm/yarn/pnpm run <script>` on a package.json project.

Detects the package manager from lockfiles (package-lock.json → npm,
yarn.lock → yarn, pnpm-lock.yaml → pnpm). The run_config's command can
override the script name; default is `dev` if present, else `start`.

Exposes an `http://{HOST}:{PORT}` URL on the card when PORT / HOST are
set in the project's variables.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import (
    DetectResult,
    InstallStep,
    LaunchSpec,
    RunConfig,
    default_focus,
    default_liveness,
    default_terminate,
    env_from_variables,
    substitute_vars,
)

if TYPE_CHECKING:
    from ..process_tracker import ProcessRecord
    from ..project import Project


class NodeJsHandler:
    name = "nodejs"
    display_name = "Node.js"
    icon = "nodedotjs"
    long_running = True
    supports_versions = True
    default_process_name_pattern = "node"
    default_output_mode = "console"
    produces_install_steps = True
    universal_folders: tuple[str, ...] = ()

    def build_launch(
        self,
        project: "Project",
        run_config: RunConfig,
        merged_vars: dict[str, Any],
        **_extras: Any,
    ) -> LaunchSpec:
        pm = _detect_package_manager(project.folder)
        script = (
            substitute_vars(run_config.command, merged_vars)
            if run_config.command
            else _default_script(project.folder)
        )
        # `npm run <script>` / `pnpm run <script>` / `yarn <script>`
        if pm == "yarn":
            argv = [pm, script]
        else:
            argv = [pm, "run", script]
        argv.extend(substitute_vars(a, merged_vars) for a in run_config.args)

        cwd = project.folder
        if run_config.cwd:
            cwd = project.folder / run_config.cwd

        return LaunchSpec(
            argv=argv,
            cwd=cwd,
            env=env_from_variables(merged_vars),
            shell=False,
            process_name_pattern=self.default_process_name_pattern,
            run_config_name=run_config.name,
            artifact_filename="package.json",
        )

    def default_run_config(self, project: "Project") -> RunConfig:
        return RunConfig(name="default", command=None, long_running=True)

    def liveness(self, record: "ProcessRecord") -> bool:
        return default_liveness(record)

    def focus(self, record: "ProcessRecord") -> bool:
        return default_focus(record)

    def terminate(self, record: "ProcessRecord", timeout: float) -> bool:
        return default_terminate(record, timeout)

    def install_steps(self, project_folder: Path) -> list[InstallStep]:
        """Run the project's package-manager install if package.json exists.

        Reuses the same lockfile-driven `_detect_package_manager`
        helper that `build_launch` uses, so a project that sets up
        with pnpm at scaffold time gets `pnpm install` here, not
        `npm install`. Skips entirely when there's no package.json
        (e.g. a scaffolded project that errored before writing one).
        """
        if not (project_folder / "package.json").is_file():
            return []
        pm = _detect_package_manager(project_folder)
        return [
            InstallStep(
                argv=[pm, "install"],
                label=f"Install dependencies ({pm} install)",
                requires_command=pm,
            ),
        ]

    def detect(self, folder: Path) -> DetectResult:
        if not (folder / "package.json").is_file():
            return DetectResult(confidence=0.0)
        signals = ["found package.json"]
        score = 0.6
        if (folder / ".nvmrc").is_file() or (folder / ".node-version").is_file():
            score = min(0.95, score + 0.1)
            signals.append("found .nvmrc / .node-version")
        if (folder / "node_modules").is_dir():
            score = min(0.95, score + 0.1)
            signals.append("found node_modules")
        try:
            with (folder / "package.json").open("r", encoding="utf-8") as f:
                pkg = json.load(f)
            if isinstance(pkg, dict) and isinstance(pkg.get("scripts"), dict):
                score = min(0.95, score + 0.1)
                signals.append("package.json declares scripts")
        except (json.JSONDecodeError, OSError):
            pass
        for cfg in ("next.config.js", "next.config.ts", "vite.config.js", "vite.config.ts"):
            if (folder / cfg).is_file():
                score = min(0.95, score + 0.1)
                signals.append(f"found {cfg}")
                break
        return DetectResult(confidence=score, signals=signals)

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
        pm = _detect_package_manager(project.folder)
        script = _default_script(project.folder)
        if pm == "yarn":
            return f"$ yarn {script}"
        return f"$ {pm} run {script}"


def _detect_package_manager(folder: Path) -> str:
    if (folder / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (folder / "yarn.lock").is_file():
        return "yarn"
    return "npm"


def _default_script(folder: Path) -> str:
    try:
        with (folder / "package.json").open("r", encoding="utf-8") as f:
            pkg = json.load(f)
    except (json.JSONDecodeError, OSError):
        return "start"
    scripts = pkg.get("scripts") if isinstance(pkg, dict) else None
    if isinstance(scripts, dict):
        if "dev" in scripts:
            return "dev"
        if "start" in scripts:
            return "start"
    return "start"


HANDLER = NodeJsHandler()
