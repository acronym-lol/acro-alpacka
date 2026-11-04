"""New-project creation logic (headless — UI lives separately).

Steps:
  1. Create the project folder.
  2. Copy the template .toe into it with the chosen name.
  3. Write a fresh alpacka.yml (AcroCore fills kind_data.touchdesigner.td_version on first save for TD projects).
  4. Optionally `git init`.
  5. Register with the library.

AcroCore's `onSave()` handles folder scaffolding (`scripts/`, `content/`, etc.),
`.gitignore`, `.mcp.json`, `CLAUDE.md`, and skills the first time TD saves
the project. The launcher doesn't duplicate that work.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import (
    approval,
    kinds,
    library,
    project,
    template_apply,
    template_scaffold,
    templates,
    universal_bootstrap,
)


class CreateError(RuntimeError):
    pass


@dataclass
class CreateOptions:
    name: str
    parent_folder: Path
    template_name: str
    # Project kind — drives which template file we clone (if any) and which
    # kind-specific fields populate alpacka.yml. For touchdesigner, the
    # template's .toe gets copied into the project folder.
    kind: str = "touchdesigner"
    td_version: str = ""  # TD-only; e.g. "2025.32280"
    td_major: str = ""  # TD-only; e.g. "099"
    description: str = ""
    tags: list[str] = field(default_factory=list)
    # Seeded by the template's declared variables: — user sees the
    # defaults in the New Project dialog. Persisted into alpacka.yml.
    variables: dict[str, object] = field(default_factory=dict)
    # Collected from the template's scaffold.inputs (if any). Fed to the
    # external CLI at scaffold time, then discarded — NOT written to
    # alpacka.yml. Only meaningful when the chosen template is bundled
    # (builtin=1) and declares a `scaffold:` block.
    scaffold_values: dict[str, object] = field(default_factory=dict)
    # Optional callable that runs the scaffold subprocess. Receives
    # (argv, cwd). Returns an exit code (0 = success) or None on user
    # cancel. When None, `scaffold:` blocks are ignored (headless /
    # scripted callers). The New Project dialog supplies a runner that
    # wraps `ScaffoldProgressDialog`.
    scaffold_runner: Callable[[list[str], Path], "int | None"] | None = None
    # When True, ALPACKA runs dependency-install steps after the
    # template apply (and after scaffold, if any). The steps come from
    # `KindHandler.install_steps(project_folder)` — Python: venv
    # create + `pip install -e .`; NodeJS: `<pm> install`; etc.
    # Failure of any step is non-fatal — the project stays registered
    # so the user can retry manually.
    install_dependencies: bool = True
    # Scaffolding toggles (universal bootstrap pieces users might skip).
    # `create_folders` covers the baseline per-kind folder set.
    create_folders: bool = True
    create_readme: bool = True
    # Git options.
    init_git: bool = True
    git_remote_url: str = ""
    # Whether to launch immediately after creation. The dialog handles
    # the actual launch via the library-view launch plumbing.
    launch_after_create: bool = True
    # How launched stdout/stderr should be surfaced. Empty string means
    # "use the kind handler's default" — see Project.effective_output_mode.
    # Concrete values: "console" / "inline" / "none". Persisted only when
    # explicitly set so default-using projects keep alpacka.yml minimal.
    output_mode: str = ""


@dataclass
class CreateResult:
    row_id: int
    project: project.Project
    should_launch: bool = False


def create_project(opts: CreateOptions) -> CreateResult:
    _validate(opts)

    # Template is optional for non-TD kinds — a plain "minimal" project
    # just gets a folder + alpacka.yml with no starter files.
    tmpl = None
    if opts.template_name:
        tmpl = templates.get_by_name(opts.template_name)
        if tmpl is None:
            raise CreateError(f"Template not found: {opts.template_name}")
        if not tmpl.source_exists:
            raise CreateError(
                f"Template source file is missing: {tmpl.toe_path or '(unset)'}"
            )

    project_folder = opts.parent_folder / opts.name
    if project_folder.exists():
        raise CreateError(f"Folder already exists: {project_folder}")

    project_folder.mkdir(parents=True, exist_ok=False)

    # Load the template's config up-front so we can use its `kind_data:`
    # to seed the new project's alpacka.yml. Subsequent stages (scaffold,
    # file-copy actions) reuse the same loaded config.
    config: "template_apply.TemplateConfig | None" = None
    if tmpl is not None and tmpl.toe_path:
        try:
            config = template_apply.resolve_config(
                Path(tmpl.toe_path), trusted=bool(tmpl.builtin)
            )
        except template_apply.TemplateConfigError as exc:
            _rollback_folder(project_folder)
            raise CreateError(f"Template load failed: {exc}") from exc

    toe_filename = ""
    if opts.kind == "touchdesigner" and tmpl is not None:
        toe_filename = f"{opts.name}.toe"
        try:
            shutil.copyfile(tmpl.toe_path, project_folder / toe_filename)
        except OSError as exc:
            _rollback_folder(project_folder)
            raise CreateError(f"Failed to copy template: {exc}") from exc

    try:
        proj = project.create_new(
            project_folder,
            name=opts.name,
            template=opts.template_name,
            toe_file=toe_filename,
            description=opts.description,
            tags=list(opts.tags),
            kind=opts.kind,
        )
        if opts.kind == "touchdesigner":
            # Pin the requested TD version — AcroCore will overwrite these on the
            # first save, but we want launch to work even before the first save.
            proj.td_version = opts.td_version
            proj.td_major = opts.td_major
        proj.variables = dict(opts.variables)
        # User-picked output mode (empty = "use kind default" — see
        # Project.effective_output_mode for resolution).
        proj.output_mode = str(opts.output_mode or "")
        # Seed kind-specific defaults from the template (e.g. python's
        # `kind_data.python.entry = main.py`). Project's own kind_data
        # wins over template's if there's a collision.
        if config is not None:
            for kind_name, fields in config.kind_data.items():
                target = proj.kind_data.setdefault(kind_name, {})
                for key, value in fields.items():
                    target.setdefault(key, value)
        project.save(proj)
    except OSError as exc:
        _rollback_folder(project_folder)
        raise CreateError(f"Failed to write alpacka.yml: {exc}") from exc

    # Universal bootstrap — baseline .gitignore always; per-kind folder
    # list comes from the kind handler.
    try:
        universal_bootstrap.apply(
            project_folder,
            create_folders=opts.create_folders,
            kind=opts.kind,
        )
    except OSError as exc:
        _rollback_folder(project_folder)
        raise CreateError(f"Universal bootstrap failed: {exc}") from exc

    # Template-specific actions via template.yml if present. Only applies
    # when a template is set and it resolves to a template file on disk.
    # Scaffold blocks (external CLIs) are only honored for bundled
    # templates — user-added templates have their scaffold blocks stripped
    # at `_load_config` time via `trusted=False`.
    # (`config` was loaded up-front for kind_data seeding; reuse here.)
    if tmpl is not None and tmpl.toe_path:
        if config is not None and config.scaffold is not None:
            try:
                _run_scaffold(config.scaffold, project_folder, opts)
            except CreateError:
                _rollback_folder(project_folder)
                raise
            except Exception as exc:  # noqa: BLE001
                _rollback_folder(project_folder)
                raise CreateError(f"Scaffold failed: {exc}") from exc

        if config is not None:
            try:
                context = template_apply.ApplyContext(
                    project_name=opts.name,
                    project_folder=project_folder,
                    toe_file=toe_filename,
                    variables=dict(opts.variables),
                )
                template_apply.apply(config, project_folder, context)
            except (template_apply.TemplateConfigError, OSError) as exc:
                _rollback_folder(project_folder)
                raise CreateError(f"Template apply failed: {exc}") from exc
        elif opts.kind != "touchdesigner":
            # Sourceless-or-yml-less folder template: copy the source tree
            # verbatim into the project folder. This is the zero-config
            # path for user-added templates that don't ship a template.yml
            # (just "I have a folder of files I want to clone").
            src = Path(tmpl.toe_path)
            if src.is_dir():
                try:
                    _copytree_into(src, project_folder)
                except OSError as exc:
                    _rollback_folder(project_folder)
                    raise CreateError(
                        f"Failed to copy template folder: {exc}"
                    ) from exc

    # Dependency install (Python venv + pip; NodeJS pm install; etc.) —
    # runs AFTER scaffold + template_apply so the manifests they wrote
    # (pyproject.toml, package.json) are visible to the kind handler.
    # Non-fatal: a failed install leaves the project registered.
    if (
        opts.install_dependencies
        and opts.scaffold_runner is not None
    ):
        handler_for_install = kinds.get(opts.kind)
        steps = handler_for_install.install_steps(project_folder)
        if steps:
            _run_install_steps(steps, project_folder, opts)

    if opts.create_readme:
        _write_readme(project_folder, opts)

    if opts.init_git:
        _git_init(project_folder)
        if opts.git_remote_url:
            _git_add_remote(project_folder, opts.git_remote_url)

    try:
        row_id = library.add(project_folder)
    except ValueError as exc:
        raise CreateError(str(exc)) from exc
    # The user just set this project up here, so its run settings are
    # approved (approval.py); later outside changes are reviewed as usual.
    approval.approve_row(row_id)

    return CreateResult(
        row_id=row_id,
        project=proj,
        should_launch=opts.launch_after_create,
    )


def _ignore_symlinks(directory: str, names: list[str]) -> set[str]:
    return {n for n in names if (Path(directory) / n).is_symlink()}


def _copytree_into(src: Path, dst: Path) -> None:
    """Recursive copy of `src`'s contents into `dst`.

    Skips the template's own ``template.yml`` (defensive — normally this
    branch only fires when there ISN'T one, but a user could rename / add
    after pointing the launcher at a folder). Everything else is copied
    verbatim; ALPACKA's own alpacka.yml (already written before this
    fires) stays intact via ``dirs_exist_ok=True``.
    """
    for entry in src.iterdir():
        # A template never brings its own per-machine overrides, and
        # symlinks are skipped so a template can't pull in files from
        # elsewhere on the machine (an SSH key, a credentials file).
        if entry.name in ("template.yml", project.LOCAL_OVERRIDE_FILENAME):
            continue
        if entry.is_symlink():
            continue
        target = dst / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target, dirs_exist_ok=True, ignore=_ignore_symlinks)
        else:
            # Do not clobber alpacka.yml — it was written by create.py
            # with the project's own metadata before this branch fires.
            if target.name == "alpacka.yml" and target.is_file():
                continue
            shutil.copyfile(entry, target)


def _run_install_steps(
    steps: list["kinds.InstallStep"],
    project_folder: Path,
    opts: CreateOptions,
) -> None:
    """Run a sequence of dependency-install commands through the scaffold runner.

    Each step gets its own progress dialog (titled by the step's
    ``label``) so the user sees what's happening. A step's failure
    aborts subsequent steps but leaves the project registered — same
    "non-fatal install failure" semantics as the old single-command
    post-install path. Headless callers (no scaffold_runner) silently
    skip the entire phase.
    """
    if opts.scaffold_runner is None:
        return
    for step in steps:
        if step.requires_command and shutil.which(step.requires_command) is None:
            # Preflight failed — surface as a progress-dialog warning
            # would be nice, but the runner only takes argv. Skip and
            # rely on the next step (or the user) noticing.
            break
        if not step.argv:
            continue
        cwd = (
            project_folder / step.cwd
            if step.cwd and step.cwd != "."
            else project_folder
        )
        try:
            exit_code = opts.scaffold_runner(list(step.argv), cwd)
        except Exception:  # noqa: BLE001
            # Crash inside the runner stays non-fatal so the project
            # still gets registered.
            return
        if exit_code is None:
            # User cancelled — abort remaining steps but keep the project.
            return
        if exit_code != 0:
            # Step failed; skip subsequent ones. The user can re-run
            # manually after diagnosing the error in the dialog output.
            return


def _run_scaffold(
    spec: "template_apply.ScaffoldSpec",
    project_folder: Path,
    opts: CreateOptions,
) -> None:
    """Preflight + execute the template's scaffold CLI in `project_folder`.

    Raises CreateError on preflight failure, runner cancellation, or
    non-zero exit from the scaffolder. Caller is responsible for rolling
    back the project folder on raise.
    """
    if opts.scaffold_runner is None:
        # Headless caller — skip scaffold silently. The template's file-copy
        # actions (if any) still run; a bundled template that ONLY declares
        # `scaffold:` produces an empty project in this path.
        return

    pf = template_scaffold.check_preflight(spec)
    if pf is not None:
        raise CreateError(pf.message)

    argv = template_scaffold.build_argv(spec, dict(opts.scaffold_values))
    if not argv:
        raise CreateError("scaffold.command resolved to an empty argv")

    cwd = project_folder / spec.cwd if spec.cwd and spec.cwd != "." else project_folder
    exit_code = opts.scaffold_runner(argv, cwd)
    if exit_code is None:
        raise CreateError("Scaffold cancelled by user.")
    if exit_code != 0:
        raise CreateError(f"Scaffold failed with exit code {exit_code}.")


def _write_readme(project_folder: Path, opts: CreateOptions) -> None:
    """Drop a minimal README.md if one doesn't already exist.

    Kept deliberately small so it reads as a starter, not something the
    user has to delete before writing their own.
    """
    readme_path = project_folder / "README.md"
    if readme_path.exists():
        return
    lines = [f"# {opts.name}", ""]
    if opts.description:
        lines += [opts.description, ""]
    readme_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def _validate(opts: CreateOptions) -> None:
    if not opts.name.strip():
        raise CreateError("Project name is required.")
    illegal = set('<>:"/\\|?*')
    if set(opts.name) & illegal:
        raise CreateError(
            f"Project name contains illegal characters: {''.join(sorted(set(opts.name) & illegal))}"
        )
    if not opts.parent_folder:
        raise CreateError("Parent folder is required.")
    if not opts.parent_folder.is_dir():
        raise CreateError(f"Parent folder does not exist: {opts.parent_folder}")
    # Only touchdesigner projects require a template (they need the .toe
    # file to launch). Other kinds can start from a blank folder.
    if opts.kind == "touchdesigner" and not opts.template_name:
        raise CreateError("Template is required for TouchDesigner projects.")


def _git_init(folder: Path) -> None:
    # Force `main` as the initial branch rather than inheriting git's built-in
    # default (`master` unless the user set `init.defaultBranch`). `-b` needs
    # git >= 2.28; on older git it errors out before creating the repo, so we
    # fall back to a plain init and repoint the unborn HEAD with symbolic-ref
    # (works on every git version).
    try:
        result = subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=folder,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return
        subprocess.run(
            ["git", "init"],
            cwd=folder,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        subprocess.run(
            ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
            cwd=folder,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        # Non-fatal: git may not be installed. The project still works.
        pass


def _git_add_remote(folder: Path, url: str) -> None:
    """Add `origin` remote after a fresh `git init`. Non-fatal on failure."""
    try:
        subprocess.run(
            ["git", "remote", "add", "origin", url],
            cwd=folder,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        pass


def _rollback_folder(folder: Path) -> None:
    try:
        shutil.rmtree(folder)
    except OSError:
        pass
