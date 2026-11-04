"""Python kind — launches a Python entry point using a detected venv.

Picks `.venv/Scripts/python.exe` (Windows) or `.venv/bin/python` (POSIX)
if present, else falls back to `python` on PATH. The entry point is
`kind_data.python.entry` (a path relative to the project folder) or the
run_config's command.
"""
from __future__ import annotations

import shutil
import sys
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


class PythonHandler:
    name = "python"
    display_name = "Python"
    icon = "python"
    long_running = False
    supports_versions = True
    default_process_name_pattern = "python"
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
        python_exec = _resolve_python(project, run_config, merged_vars)
        kind_data = project.kind_data.get("python", {})
        entry = run_config.command or str(kind_data.get("entry", "") or "")
        if not entry:
            raise RuntimeError(
                "python kind: neither run_config.command nor "
                "kind_data.python.entry is set"
            )
        entry = substitute_vars(entry, merged_vars)

        args = [substitute_vars(a, merged_vars) for a in run_config.args]
        # If entry ends in .py, run it as a script; else treat as a module.
        if entry.endswith(".py"):
            argv = [python_exec, entry, *args]
        else:
            argv = [python_exec, "-m", entry, *args]

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
            artifact_filename=Path(entry).name,
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
        """Set up `.venv/` and install declared dependencies.

        Two-step pipeline:
          1. ``<system-python> -m venv .venv`` creates the per-project
             virtualenv next to the project's source.
          2. ``.venv/{Scripts,bin}/pip install ...`` installs from
             ``pyproject.toml`` (preferred) or ``requirements.txt``.

        The system python is resolved via PATH (`python3` first, then
        `python`); we deliberately don't reuse `sys.executable` because
        the packaged ALPACKA build runs a PyInstaller-bundled
        interpreter that can't create a usable venv. Returns an empty
        list when neither dependency manifest is present, so a hand-
        rolled python project doesn't trigger spurious commands.
        """
        has_pyproject = (project_folder / "pyproject.toml").is_file()
        has_requirements = (project_folder / "requirements.txt").is_file()
        if not (has_pyproject or has_requirements):
            return []

        system_python = _resolve_system_python()
        if system_python is None:
            # Surface as zero steps — the install dialog will be hidden,
            # the project still gets created. The launch path will fail
            # later with a clearer "python not found" via the kind
            # handler if the user really has no Python on PATH.
            return []

        if sys.platform == "win32":
            venv_pip = str(project_folder / ".venv" / "Scripts" / "pip.exe")
        else:
            venv_pip = str(project_folder / ".venv" / "bin" / "pip")

        steps: list[InstallStep] = [
            InstallStep(
                argv=[system_python, "-m", "venv", ".venv"],
                label="Create virtual environment (.venv)",
                requires_command=system_python,
            ),
        ]
        # Prefer pyproject — `pip install -e .` builds + installs the
        # project itself (including any `[project.scripts]` entry
        # points). Falls back to requirements.txt for projects that
        # haven't adopted PEP 621.
        if has_pyproject:
            steps.append(
                InstallStep(
                    argv=[venv_pip, "install", "-e", "."],
                    label="Install dependencies (pip install -e .)",
                )
            )
        else:
            steps.append(
                InstallStep(
                    argv=[venv_pip, "install", "-r", "requirements.txt"],
                    label="Install dependencies (pip install -r requirements.txt)",
                )
            )
        return steps

    def detect(self, folder: Path) -> DetectResult:
        signals: list[str] = []
        score = 0.0
        if (folder / "pyproject.toml").is_file():
            score = max(score, 0.6)
            signals.append("found pyproject.toml")
        if (folder / "requirements.txt").is_file():
            score = max(score, 0.6)
            signals.append("found requirements.txt")
        if (folder / ".venv").is_dir():
            score = max(score, min(score + 0.1, 0.95))
            signals.append("found .venv")
        if (folder / "setup.py").is_file():
            score = max(score, 0.5)
            signals.append("found setup.py")
        if (folder / "__main__.py").is_file():
            score = max(score, 0.3)
            signals.append("found __main__.py")
        return DetectResult(confidence=score, signals=signals)

    def display_url(
        self, project: "Project", variables: dict[str, Any]
    ) -> str | None:
        port = variables.get("PORT")
        host = variables.get("HOST", "localhost")
        if port is not None:
            return f"http://{host}:{port}"
        return None

    def target_summary(self, project: "Project") -> str:
        kind_data = project.kind_data.get("python", {})
        entry = str(kind_data.get("entry", "") or "")
        if entry:
            return f"$ python {entry}"
        return "$ python (entry not set)"


def _resolve_python(
    project: "Project", run_config: RunConfig, merged_vars: dict[str, Any]
) -> str:
    """Pick the Python interpreter to use.

    Precedence: explicit `PYTHON_EXEC` variable > `.venv/` in the project
    folder > the user's system Python.
    """
    explicit = merged_vars.get("PYTHON_EXEC")
    if explicit:
        return substitute_vars(str(explicit), merged_vars)
    if sys.platform == "win32":
        venv = project.folder / ".venv" / "Scripts" / "python.exe"
    else:
        venv = project.folder / ".venv" / "bin" / "python"
    if venv.is_file():
        return str(venv)
    return _resolve_system_python() or "python"


def _resolve_system_python() -> str | None:
    """Find a usable Python interpreter on PATH.

    Order matters here: on Windows we prefer ``py`` (the Python
    launcher that ships with python.org installers) because the
    bare ``python.exe`` and ``python3.exe`` names are commonly
    shadowed by Microsoft Store redirector stubs that exit with
    code 9009 instead of running Python. ``py`` is unaffected by
    those stubs. On POSIX, ``python3`` wins since ``python`` is
    historically Python 2 on legacy distros.

    Returns the resolved absolute path, or None if nothing usable
    is on PATH (in which case install_steps surfaces empty and the
    user has to install Python themselves).
    """
    if sys.platform == "win32":
        candidates = ("py", "python", "python3")
    else:
        candidates = ("python3", "python")
    for name in candidates:
        path = shutil.which(name)
        if path:
            return path
    return None


HANDLER = PythonHandler()
