"""Project model + `alpacka.yml` I/O.

`alpacka.yml` is the per-project metadata file, written with ruamel.yaml
so comments and field order survive round-trips. Ownership is split:

- **Launcher-owned (top level)** — `name`, `description`, `template`,
  `kind`, `created`, `tags`, `variables`, `variable_meta`, `run_configs`,
  and the crash-relaunch config.
- **Kind-owned** — `kind_data.<kind>` belongs to the kind handler and any
  tool integration for that kind (e.g. AcroCore writes
  `kind_data.touchdesigner.td_version` / `td_major` on .toe save).
- **Shared** — `last_opened` is updated by whichever side most recently
  touched the project. Last writer wins.

`save()` loads the existing file and mutates it in place so writes from
another side (AcroCore-owned fields, user-written comments, unknown fields)
survive a launcher save.

`alpacka.local.yml` (per-machine values, often secrets) is merged over the
main file at load time and must never be written back into `alpacka.yml`.
A loaded Project therefore remembers the main file's values, the local
file's values and a snapshot of what it looked like when loaded; `save()`
writes only the fields the caller changed, and rebuilds each of those from
the main file's value so local-only values stay out.
"""
from __future__ import annotations

import copy
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from . import fsutil

PROJECT_INFO_FILENAME = "alpacka.yml"
LOCAL_OVERRIDE_FILENAME = "alpacka.local.yml"
SCHEMA_VERSION = 1
THUMBNAIL_FILENAME = ".launcher-thumb.png"
TOUCHDESIGNER_KIND = "touchdesigner"


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Project:
    """In-memory representation of a project on disk.

    `folder` is the project root; metadata lives at `folder / alpacka.yml`.
    Kind-specific data (e.g. TouchDesigner's `toe_file`, `td_version`) lives
    under `kind_data[kind]`. TD-flat accessors (`toe_file`, `td_version`,
    etc.) are compatibility properties that read/write through
    `kind_data["touchdesigner"]` so existing call sites don't need rewriting.
    """

    folder: Path
    name: str = ""
    description: str = ""
    template: str = ""
    kind: str = TOUCHDESIGNER_KIND
    # Kind-specific data. Keyed by kind name. For touchdesigner:
    # toe_file, alternate_toe_files, default_toes, td_version, td_major.
    kind_data: dict[str, dict[str, Any]] = field(default_factory=dict)
    created: str = ""
    last_opened: str = ""
    tags: list[str] = field(default_factory=list)

    launch_on_startup: bool = False
    reopen_on_crash: bool = False
    freeze_timeout_seconds: int = 30
    startup_grace_seconds: int = 60
    relaunch_delay_seconds: int = 5
    max_retries_enabled: bool = False
    max_retries: int = 3

    # How launched stdout/stderr is surfaced. Empty string = "use the kind
    # handler's default" (resolved via `effective_output_mode`). Concrete
    # values: "console" (external terminal), "inline" (in-app log pane),
    # "none" (silent — current behavior). Persisted only when explicitly
    # set so default-using projects keep alpacka.yml minimal.
    output_mode: str = ""

    # Per-project variables + optional UI metadata. See docs/templates.md.
    variables: dict[str, Any] = field(default_factory=dict)
    variable_meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Named run configurations. Each is a dict shaped by kinds.base.RunConfig.
    # An empty list means "use the kind's default".
    run_configs: list[dict[str, Any]] = field(default_factory=list)
    # Companion commands launched alongside the main process. Each entry:
    #   {name, command, enabled, oneshot, keep_alive}
    # Kind-agnostic — any project may want a sidecar. The launcher spawns
    # them after the main process, registers them in `running_processes`
    # with kind="dependency", and uses the project's output_mode for stdio.
    launch_dependencies: list[dict[str, Any]] = field(default_factory=list)

    # Unknown fields we don't model — preserved verbatim across round-trips.
    _raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # Load-time state used by `save()` to write only what changed and to
    # keep `alpacka.local.yml` values out of `alpacka.yml`. All three are
    # None for a Project built in code (create_new / bootstrap), which
    # `save()` writes in full.
    _main: dict[str, Any] | None = field(default=None, repr=False, compare=False)
    _local: dict[str, Any] | None = field(default=None, repr=False, compare=False)
    _loaded: dict[str, Any] | None = field(default=None, repr=False, compare=False)
    # Problems found while loading (e.g. an ignored alpacka.local.yml).
    load_warnings: list[str] = field(default_factory=list, repr=False, compare=False)

    @property
    def info_path(self) -> Path:
        return self.folder / PROJECT_INFO_FILENAME

    @property
    def thumbnail_path(self) -> Path:
        """Canonical path for the project's launcher thumbnail.

        Presence of `.launcher-thumb.png` at this path is the source of truth;
        no field in alpacka.yml mirrors it.
        """
        return self.folder / THUMBNAIL_FILENAME

    @property
    def has_thumbnail(self) -> bool:
        return self.thumbnail_path.is_file()

    @property
    def folder_exists(self) -> bool:
        return self.folder.is_dir()

    # --- TouchDesigner compatibility accessors ---
    # These read/write through `kind_data["touchdesigner"]` so the rest of the
    # codebase keeps compiling. Source of truth is kind_data.

    def _td(self) -> dict[str, Any]:
        return self.kind_data.setdefault(TOUCHDESIGNER_KIND, {})

    @property
    def toe_file(self) -> str:
        return str(self._td().get("toe_file", "") or "")

    @toe_file.setter
    def toe_file(self, value: str) -> None:
        self._td()["toe_file"] = str(value)

    @property
    def alternate_toe_files(self) -> list[str]:
        vals = self._td().get("alternate_toe_files") or []
        return [str(v) for v in vals]

    @alternate_toe_files.setter
    def alternate_toe_files(self, value: list[str]) -> None:
        self._td()["alternate_toe_files"] = [str(v) for v in value]

    @property
    def default_toes(self) -> list[str]:
        vals = self._td().get("default_toes") or []
        return [str(v) for v in vals]

    @default_toes.setter
    def default_toes(self, value: list[str]) -> None:
        self._td()["default_toes"] = [str(v) for v in value]

    @property
    def td_version(self) -> str:
        return str(self._td().get("td_version", "") or "")

    @td_version.setter
    def td_version(self, value: str) -> None:
        self._td()["td_version"] = str(value)

    @property
    def td_major(self) -> str:
        return str(self._td().get("td_major", "") or "")

    @td_major.setter
    def td_major(self, value: str) -> None:
        self._td()["td_major"] = str(value)

    # --- TouchDesigner launch settings ---
    # Launcher-owned sub-section under `kind_data.touchdesigner.launch_settings`.
    # Each setter pops the key on a falsy value so all-default projects don't
    # carry a launch_settings block in alpacka.yml; save() further prunes the
    # block itself when empty.

    def _td_launch(self) -> dict[str, Any]:
        return self._td().setdefault("launch_settings", {})

    def _td_launch_set(self, key: str, value: Any) -> None:
        settings = self._td_launch()
        if not value:
            settings.pop(key, None)
        else:
            settings[key] = value

    @property
    def td_text_console(self) -> bool:
        return bool(self._td_launch().get("text_console", False))

    @td_text_console.setter
    def td_text_console(self, value: bool) -> None:
        self._td_launch_set("text_console", bool(value))

    @property
    def td_disable_update_check(self) -> bool:
        return bool(self._td_launch().get("disable_update_check", False))

    @td_disable_update_check.setter
    def td_disable_update_check(self, value: bool) -> None:
        self._td_launch_set("disable_update_check", bool(value))

    @property
    def td_app_log_level(self) -> str:
        return str(self._td_launch().get("app_log_level", "") or "")

    @td_app_log_level.setter
    def td_app_log_level(self, value: str) -> None:
        self._td_launch_set("app_log_level", str(value or ""))

    @property
    def td_quick_crash(self) -> bool:
        return bool(self._td_launch().get("quick_crash", False))

    @td_quick_crash.setter
    def td_quick_crash(self, value: bool) -> None:
        self._td_launch_set("quick_crash", bool(value))

    @property
    def td_disable_crash_handlers(self) -> bool:
        return bool(self._td_launch().get("disable_crash_handlers", False))

    @td_disable_crash_handlers.setter
    def td_disable_crash_handlers(self, value: bool) -> None:
        self._td_launch_set("disable_crash_handlers", bool(value))

    @property
    def td_ten_bit_color(self) -> bool:
        return bool(self._td_launch().get("ten_bit_color", False))

    @td_ten_bit_color.setter
    def td_ten_bit_color(self, value: bool) -> None:
        self._td_launch_set("ten_bit_color", bool(value))

    @property
    def td_double_draw(self) -> bool:
        return bool(self._td_launch().get("double_draw", False))

    @td_double_draw.setter
    def td_double_draw(self, value: bool) -> None:
        self._td_launch_set("double_draw", bool(value))

    @property
    def td_editor(self) -> str:
        return str(self._td_launch().get("editor", "") or "")

    @td_editor.setter
    def td_editor(self, value: str) -> None:
        self._td_launch_set("editor", str(value or ""))

    @property
    def td_table_editor(self) -> str:
        return str(self._td_launch().get("table_editor", "") or "")

    @td_table_editor.setter
    def td_table_editor(self, value: str) -> None:
        self._td_launch_set("table_editor", str(value or ""))

    @property
    def td_web_launch(self) -> str:
        """Path to the browser TD should use when opening help / docs links.

        Per the TouchDesigner wiki, ``TOUCH_WEB_LAUNCH`` is a path to a
        browser executable, not a boolean. Empty ⇒ env var omitted ⇒
        TD uses the system default browser.
        """
        return str(self._td_launch().get("web_launch", "") or "")

    @td_web_launch.setter
    def td_web_launch(self, value: str) -> None:
        self._td_launch_set("web_launch", str(value or ""))

    @property
    def td_help_delay(self) -> int | None:
        """Optional pop-up help delay in seconds.

        ``None`` ⇒ unset (TD default). ``0`` ⇒ pop-up help disabled
        (a meaningful value per the wiki, distinct from "unset"). Any
        other int ⇒ that many seconds before the pop-up appears.
        """
        raw = self._td_launch().get("help_delay")
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    @td_help_delay.setter
    def td_help_delay(self, value: int | None) -> None:
        settings = self._td_launch()
        if value is None:
            settings.pop("help_delay", None)
        else:
            settings["help_delay"] = int(value)

    @property
    def td_midi_chan_override(self) -> str:
        """Space-separated remap string, e.g. ``"0 2 4"`` (input1 input2 output).

        Stored verbatim and forwarded to TD as ``TOUCH_MIDI_CHAN_OVERRIDE``.
        Empty ⇒ env var omitted.
        """
        return str(self._td_launch().get("midi_chan_override", "") or "")

    @td_midi_chan_override.setter
    def td_midi_chan_override(self, value: str) -> None:
        self._td_launch_set("midi_chan_override", str(value or "").strip())

    @property
    def td_extra_args(self) -> list[str]:
        vals = self._td_launch().get("extra_args") or []
        return [str(v) for v in vals]

    @td_extra_args.setter
    def td_extra_args(self, value: list[str]) -> None:
        coerced = [str(v) for v in (value or [])]
        self._td_launch_set("extra_args", coerced)

    # --- TouchDesigner-specific helpers (kept here for now; will move to
    # the touchdesigner kind handler in a later commit). ---

    @property
    def toe_path(self) -> Path | None:
        return self.folder / self.toe_file if self.toe_file else None

    @property
    def toe_exists(self) -> bool:
        tp = self.toe_path
        return bool(tp and tp.is_file())

    @property
    def is_stale(self) -> bool:
        if not self.folder_exists:
            return True
        # Kind-specific readiness: TD needs a .toe file to launch; other
        # kinds are ready as long as the folder exists (their specific
        # config — entry point, package.json, etc. — is validated at
        # launch time rather than pre-flighted here).
        if self.kind == TOUCHDESIGNER_KIND:
            return not self.toe_exists
        return False

    def alternate_paths(self) -> list[Path]:
        return [self.folder / name for name in self.alternate_toe_files]

    def all_toe_filenames(self) -> list[str]:
        names: list[str] = []
        if self.toe_file:
            names.append(self.toe_file)
        for alt in self.alternate_toe_files:
            if alt not in names:
                names.append(alt)
        return names

    def effective_launch_toes(self) -> list[str]:
        known = set(self.all_toe_filenames())
        if self.default_toes:
            chosen = [t for t in self.default_toes if t in known]
            if chosen:
                return chosen
        return [self.toe_file] if self.toe_file else []

    def resolve_toe(self, relative_name: str | None) -> Path | None:
        if not relative_name:
            return self.toe_path
        return self.folder / relative_name

    def effective_output_mode(self, handler: Any) -> str:
        """Resolve the output mode for this project.

        Returns the user-set `output_mode` if non-empty, else falls back
        to `handler.default_output_mode` (the kind's recommended default).
        Defaults to ``"none"`` if the handler doesn't declare one — keeps
        legacy behavior intact for any handler that hasn't migrated.
        """
        return self.output_mode or getattr(handler, "default_output_mode", "none")


def load(folder: Path) -> Project | None:
    """Return a Project if `alpacka.yml` exists in `folder`, else None.

    If `alpacka.local.yml` sits alongside, its fields are merged on top of
    the main file (key-aware: run_configs merge by name, variables dict-
    merge, etc.). Local overrides never get written back — the file is a
    read-only sidecar for secrets / per-machine values.
    """
    folder = Path(folder)
    info = folder / PROJECT_INFO_FILENAME
    if not info.is_file():
        return None
    try:
        with info.open("r", encoding="utf-8") as f:
            data = _yaml().load(f)
        if not isinstance(data, dict):
            return None
        main = _plain(data)
    except Exception:  # noqa: BLE001 — unreadable / hostile YAML: treat as missing
        return None

    # Merge in local overrides if present. Local file is read-only from
    # the launcher's perspective; save() never touches it.
    warnings: list[str] = []
    local: dict[str, Any] = {}
    local_path = folder / LOCAL_OVERRIDE_FILENAME
    if local_path.is_file():
        try:
            with local_path.open("r", encoding="utf-8") as f:
                local_data = _yaml().load(f)
            if isinstance(local_data, dict):
                local = _plain(local_data)
        except Exception:  # noqa: BLE001
            warnings.append(f"{LOCAL_OVERRIDE_FILENAME} is unreadable and was ignored.")
            local = {}

    merged = main
    if local:
        try:
            merged = merge_local(main, local)
        except ValueError as exc:
            warnings.append(f"{LOCAL_OVERRIDE_FILENAME} was ignored: {exc}")
            local = {}
            merged = main

    try:
        project = _from_dict(folder, merged)
    except Exception:  # noqa: BLE001
        return None
    project._main = main
    project._local = local
    project._loaded = _snapshot(project)
    project.load_warnings = warnings
    return project


def merge_local(main: dict, local: dict) -> dict:
    """Key-aware merge of a local override over the main config.

    - Top-level scalars: local wins.
    - `variables`: dict-merge (local keys override, others preserved).
    - `variable_meta`: same as variables.
    - `run_configs`: merge by `name` (override same-named entries; append
      new names).
    - `kind_data.<kind>`: dict-merge per kind; `launch_settings` inside it
      is merged per key too.
    - `kind`: must match main or be absent; raise ValueError on mismatch.
    """
    result: dict = dict(main)  # shallow copy; mutate nested below

    # Kind safety — never let a local override change the project kind.
    local_kind = local.get("kind")
    if local_kind is not None and local_kind != result.get("kind"):
        raise ValueError(
            f"alpacka.local.yml kind '{local_kind}' does not match "
            f"alpacka.yml kind '{result.get('kind')}'"
        )

    for key, local_value in local.items():
        if key == "kind":
            continue
        if key == "variables" and isinstance(local_value, dict):
            merged_vars = dict(result.get("variables") or {})
            merged_vars.update(local_value)
            result["variables"] = merged_vars
            continue
        if key == "variable_meta" and isinstance(local_value, dict):
            merged_meta = dict(result.get("variable_meta") or {})
            for vname, vmeta in local_value.items():
                base_meta = dict(merged_meta.get(vname) or {})
                if isinstance(vmeta, dict):
                    base_meta.update(vmeta)
                merged_meta[vname] = base_meta
            result["variable_meta"] = merged_meta
            continue
        if key == "run_configs" and isinstance(local_value, list):
            base_list = list(result.get("run_configs") or [])
            base_by_name = {
                (c.get("name") if isinstance(c, dict) else None): i
                for i, c in enumerate(base_list)
            }
            for entry in local_value:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                if name in base_by_name:
                    merged = dict(base_list[base_by_name[name]])
                    merged.update(entry)
                    base_list[base_by_name[name]] = merged
                else:
                    base_list.append(dict(entry))
            result["run_configs"] = base_list
            continue
        if key == "launch_dependencies" and isinstance(local_value, list):
            base_list = list(result.get("launch_dependencies") or [])
            base_by_name = {
                (c.get("name") if isinstance(c, dict) else None): i
                for i, c in enumerate(base_list)
            }
            for entry in local_value:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                if name in base_by_name and name:
                    merged = dict(base_list[base_by_name[name]])
                    merged.update(entry)
                    base_list[base_by_name[name]] = merged
                else:
                    base_list.append(dict(entry))
            result["launch_dependencies"] = base_list
            continue
        if key == "kind_data" and isinstance(local_value, dict):
            merged_kd = dict(result.get("kind_data") or {})
            for kind_name, kind_fields in local_value.items():
                base_kind = dict(merged_kd.get(kind_name) or {})
                if isinstance(kind_fields, dict):
                    for fkey, fval in kind_fields.items():
                        base_val = base_kind.get(fkey)
                        if (
                            fkey == "launch_settings"
                            and isinstance(fval, dict)
                            and isinstance(base_val, dict)
                        ):
                            base_kind[fkey] = {**base_val, **fval}
                        else:
                            base_kind[fkey] = fval
                merged_kd[kind_name] = base_kind
            result["kind_data"] = merged_kd
            continue
        # Default: local scalar wins.
        result[key] = local_value

    return result


_KNOWN_FIELDS = frozenset({
    "schema",
    "name",
    "description",
    "template",
    "kind",
    "kind_data",
    "created",
    "last_opened",
    "tags",
    "launch_on_startup",
    "reopen_on_crash",
    "freeze_timeout_seconds",
    "startup_grace_seconds",
    "relaunch_delay_seconds",
    "max_retries_enabled",
    "max_retries",
    "variables",
    "variable_meta",
    "run_configs",
    "launch_dependencies",
    "output_mode",
})


def save(project: Project) -> None:
    """Merge-save `alpacka.yml`, preserving comments and unknown fields.

    Loads the on-disk file (if present), mutates the CommentedMap in place,
    and re-dumps. Kind-specific sections (`kind_data.<kind>`) are merged
    per key so writes from another side (AcroCore) survive.

    For a Project that came from `load()`, only fields that differ from the
    load-time snapshot are written, and each one is rebuilt from the main
    file's value (`_unmerge`), so values that came from `alpacka.local.yml`
    never land in `alpacka.yml`. A Project built in code is written in full.
    """
    from ruamel.yaml.comments import CommentedMap

    project.folder.mkdir(parents=True, exist_ok=True)
    yaml = _yaml()

    existing: Any = None
    if project.info_path.is_file():
        try:
            with project.info_path.open("r", encoding="utf-8") as f:
                existing = yaml.load(f)
        except Exception:
            existing = None
    if not isinstance(existing, dict):
        existing = CommentedMap()

    loaded = project._loaded
    main = project._main or {}
    local = project._local or {}

    existing["schema"] = SCHEMA_VERSION
    for key, value in _to_dict(project).items():
        if loaded is not None:
            before = loaded["top"].get(key, _MISSING)
            if value == before:
                continue
            if key in ("run_configs", "launch_dependencies"):
                value = _unmerge_named_list(
                    main.get(key, _MISSING), local.get(key, _MISSING), before, value
                )
            else:
                value = _unmerge(
                    main.get(key, _MISSING), local.get(key, _MISSING), before, value
                )
        # `output_mode == ""` means "use the kind handler's default" — don't
        # persist it so default-using projects keep alpacka.yml minimal,
        # and so toggling back to "Default for kind" cleans up after itself.
        if value is _MISSING or (key == "output_mode" and not value):
            existing.pop(key, None)
            continue
        existing[key] = value

    # Unknown fields we round-tripped into Project._raw. They are already in
    # `existing` (re-read from disk), so only a fresh Project or a changed
    # value needs writing.
    loaded_raw = loaded["raw"] if loaded is not None else {}
    for key, value in project._raw.items():
        if key in _KNOWN_FIELDS:
            continue
        if loaded is not None:
            before = loaded_raw.get(key, _MISSING)
            if value == before:
                continue
            value = _unmerge(main.get(key, _MISSING), local.get(key, _MISSING), before, value)
        if value is _MISSING:
            existing.pop(key, None)
        else:
            existing[key] = value

    # Per-kind merge for kind_data: keys set by other integrations (e.g. AcroCore
    # writes into kind_data.touchdesigner after a save) must not be clobbered
    # by fields the launcher doesn't model. We mutate the existing CommentedMap
    # in place rather than reassigning the key, so before-key comments survive.
    loaded_kd = loaded["kind_data"] if loaded is not None else {}
    main_kd = main.get("kind_data") if isinstance(main.get("kind_data"), dict) else {}
    local_kd = local.get("kind_data") if isinstance(local.get("kind_data"), dict) else {}
    for kind_name, kind_fields in project.kind_data.items():
        before_kind = loaded_kd.get(kind_name) or {}
        main_kind = main_kd.get(kind_name) if isinstance(main_kd.get(kind_name), dict) else {}
        local_kind = local_kd.get(kind_name) if isinstance(local_kd.get(kind_name), dict) else {}
        writes: dict[str, Any] = {}
        for k, v in kind_fields.items():
            if loaded is not None:
                before = before_kind.get(k, _MISSING)
                if v == before:
                    continue
                v = _unmerge(main_kind.get(k, _MISSING), local_kind.get(k, _MISSING), before, v)
            writes[k] = v
        removed = [k for k in before_kind if loaded is not None and k not in kind_fields]
        if not writes and not removed:
            continue
        if not isinstance(existing.get("kind_data"), dict):
            existing["kind_data"] = CommentedMap()
        existing_kd = existing["kind_data"]
        if not isinstance(existing_kd.get(kind_name), dict):
            existing_kd[kind_name] = CommentedMap()
        current_kind = existing_kd[kind_name]
        for k, v in writes.items():
            # `launch_settings` is launcher-owned; an empty dict means
            # "all defaults — don't carry an empty block in alpacka.yml".
            if v is _MISSING or (k == "launch_settings" and isinstance(v, dict) and not v):
                current_kind.pop(k, None)
                continue
            current_kind[k] = v
        for k in removed:
            current_kind.pop(k, None)

    _dump(project.info_path, existing, yaml)

    # The main file now holds what was just written; later saves on this
    # object diff against it.
    project._main = _plain(existing)
    project._local = local if loaded is not None else {}
    project._loaded = _snapshot(project)


def _dump(path: Path, data: Any, yaml: YAML) -> None:
    buf = io.StringIO()
    yaml.dump(data, buf)
    fsutil.write_text_atomic(path, buf.getvalue())


class _Missing:
    """Marker for "key not present" (distinct from a stored None)."""

    def __repr__(self) -> str:
        return "<missing>"


_MISSING: Any = _Missing()


def _get(container: Any, key: Any) -> Any:
    if isinstance(container, dict):
        return container.get(key, _MISSING)
    return _MISSING


def _unmerge(main: Any, local: Any, before: Any, current: Any) -> Any:
    """Value to store in alpacka.yml for a field the caller changed.

    `before` is the merged (main + local) value at load time and `current`
    the value now. Parts the caller left unchanged keep the main file's
    value (``_MISSING`` = not in the main file), so an unchanged value that
    came from alpacka.local.yml is never copied into alpacka.yml.
    """
    if current == before:
        return main
    if isinstance(current, dict) and isinstance(before, dict):
        out = dict(main) if isinstance(main, dict) else {}
        for k, v in current.items():
            r = _unmerge(_get(main, k), _get(local, k), before.get(k, _MISSING), v)
            if r is _MISSING:
                out.pop(k, None)
            else:
                out[k] = r
        for k in before:
            if k not in current:
                out.pop(k, None)
        return out
    return current


def _unmerge_named_list(main: Any, local: Any, before: Any, current: Any) -> Any:
    """`_unmerge` for run_configs / launch_dependencies, matched by `name`."""
    if current == before:
        return main

    def by_name(items: Any) -> dict[str, Any]:
        if not isinstance(items, list):
            return {}
        return {
            e["name"]: e for e in items if isinstance(e, dict) and e.get("name")
        }

    main_by, local_by, before_by = by_name(main), by_name(local), by_name(before)
    local_unnamed = [
        e for e in (local if isinstance(local, list) else [])
        if isinstance(e, dict) and not e.get("name")
    ]
    out: list[Any] = []
    for entry in current if isinstance(current, list) else []:
        name = entry.get("name") if isinstance(entry, dict) else None
        if name and name in before_by:
            r = _unmerge(
                main_by.get(name, _MISSING), local_by.get(name, _MISSING),
                before_by[name], entry,
            )
            if r is not _MISSING:
                out.append(r)
        elif not name and entry in local_unnamed:
            continue  # an unnamed entry that only exists in alpacka.local.yml
        else:
            out.append(entry)
    return out


def _snapshot(project: Project) -> dict[str, Any]:
    return {
        "top": copy.deepcopy(_to_dict(project)),
        "kind_data": copy.deepcopy(project.kind_data),
        "raw": copy.deepcopy(project._raw),
    }


def create_new(
    folder: Path,
    *,
    name: str,
    template: str,
    toe_file: str,
    description: str = "",
    tags: list[str] | None = None,
    kind: str = TOUCHDESIGNER_KIND,
) -> Project:
    """Create a Project dataclass and write a fresh `alpacka.yml`.

    Kind-owned fields (e.g. `td_version`, `td_major` for touchdesigner) are
    left empty; the corresponding tool integration populates them on first
    save.
    """
    now = _now()
    project = Project(
        folder=Path(folder),
        name=name,
        description=description,
        template=template,
        kind=kind,
        created=now,
        last_opened=now,
        tags=list(tags or []),
    )
    if kind == TOUCHDESIGNER_KIND and toe_file:
        project.toe_file = toe_file
    save(project)
    return project


def bootstrap_from_existing(
    folder: Path,
    *,
    toe_file: str,
    td_version: str,
    td_major: str,
    name: str | None = None,
    alternate_toe_files: list[str] | None = None,
    tags: list[str] | None = None,
) -> Project:
    """Create an `alpacka.yml` for an existing TD project discovered on disk.

    Used by the "Add Existing" flow after toe_inspect extracts the version.
    """
    now = _now()
    project = Project(
        folder=Path(folder),
        name=name or Path(folder).name,
        kind=TOUCHDESIGNER_KIND,
        created=now,
        last_opened=now,
        tags=list(tags or []),
    )
    project.toe_file = toe_file
    project.alternate_toe_files = list(alternate_toe_files or [])
    project.td_version = td_version
    project.td_major = td_major
    save(project)
    return project


def touch_last_opened(project: Project) -> None:
    """Update `last_opened` to now and persist just that one key.

    Runs on every launch with a Project that may be minutes old, so it must
    not write anything else (no stale fields, nothing from the local file).
    Does nothing if alpacka.yml is gone or unreadable.
    """
    project.last_opened = _now()
    yaml = _yaml()
    try:
        with project.info_path.open("r", encoding="utf-8") as f:
            existing = yaml.load(f)
    except Exception:  # noqa: BLE001
        return
    if not isinstance(existing, dict):
        return
    existing["last_opened"] = project.last_opened
    _dump(project.info_path, existing, yaml)
    if project._main is not None:
        project._main["last_opened"] = project.last_opened
    if project._loaded is not None:
        project._loaded["top"]["last_opened"] = project.last_opened


@dataclass(frozen=True)
class LocalOverrides:
    """Which values in a loaded Project come from alpacka.local.yml.

    The UI uses this to show those values read-only: they can only be
    changed in the local file, and `save()` never copies them into
    alpacka.yml.
    """

    top: frozenset[str] = frozenset()
    variables: frozenset[str] = frozenset()
    # Run config name -> the keys alpacka.local.yml sets on it.
    run_configs: dict[str, frozenset[str]] = field(default_factory=dict)
    dependency_indexes: frozenset[int] = frozenset()
    kind_data: dict[str, frozenset[str]] = field(default_factory=dict)
    launch_settings: frozenset[str] = frozenset()

    @property
    def any(self) -> bool:
        return bool(
            self.top or self.variables or self.run_configs
            or self.dependency_indexes or self.kind_data or self.launch_settings
        )


def local_overrides(project: Project) -> LocalOverrides:
    local = project._local or {}
    if not local:
        return LocalOverrides()
    main = project._main or {}

    variables = set()
    for key in ("variables", "variable_meta"):
        if isinstance(local.get(key), dict):
            variables |= {str(k) for k in local[key]}

    run_configs = {
        str(e["name"]): frozenset(str(k) for k in e if k != "name")
        for e in local.get("run_configs") or []
        if isinstance(e, dict) and e.get("name")
    }

    # Mirror merge_local's placement: a named local entry replaces the main
    # entry of the same name; anything else is appended after main's.
    main_deps = [e for e in main.get("launch_dependencies") or [] if isinstance(e, dict)]
    index_by_name = {e.get("name"): i for i, e in enumerate(main_deps) if e.get("name")}
    deps: set[int] = set()
    next_index = len(main_deps)
    for entry in local.get("launch_dependencies") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if name and name in index_by_name:
            deps.add(index_by_name[name])
        else:
            deps.add(next_index)
            next_index += 1

    kind_data: dict[str, frozenset[str]] = {}
    launch_settings: frozenset[str] = frozenset()
    if isinstance(local.get("kind_data"), dict):
        for kind_name, fields in local["kind_data"].items():
            if not isinstance(fields, dict):
                continue
            kind_data[str(kind_name)] = frozenset(str(k) for k in fields)
            if kind_name == TOUCHDESIGNER_KIND and isinstance(fields.get("launch_settings"), dict):
                launch_settings = frozenset(str(k) for k in fields["launch_settings"])

    collections = {"variables", "variable_meta", "run_configs", "launch_dependencies", "kind_data", "kind"}
    top = frozenset(str(k) for k in local if k not in collections)
    return LocalOverrides(
        top=top,
        variables=frozenset(variables),
        run_configs=run_configs,
        dependency_indexes=frozenset(deps),
        kind_data=kind_data,
        launch_settings=launch_settings,
    )


def enabled_launch_dependencies(project: Project) -> list[dict[str, Any]]:
    """Return only the deps the user wants spawned.

    `enabled` defaults to True so an entry that omits the key is included.
    Empty-command entries are dropped — a row with no command is a stub
    the UI hasn't filled in yet.
    """
    out: list[dict[str, Any]] = []
    for dep in project.launch_dependencies:
        if not isinstance(dep, dict):
            continue
        if not bool(dep.get("enabled", True)):
            continue
        if not str(dep.get("command", "") or "").strip():
            continue
        out.append(dep)
    return out


def _from_dict(folder: Path, data: dict[str, Any]) -> Project:
    raw = {k: _plain(v) for k, v in data.items() if k not in _KNOWN_FIELDS}
    tags = data.get("tags") or []
    if not isinstance(tags, list):
        tags = []

    kind_data_raw = data.get("kind_data") or {}
    kind_data: dict[str, dict[str, Any]] = {}
    if isinstance(kind_data_raw, dict):
        for k, v in kind_data_raw.items():
            if isinstance(v, dict):
                kind_data[str(k)] = {str(kk): _plain(vv) for kk, vv in v.items()}

    variables = data.get("variables") or {}
    if not isinstance(variables, dict):
        variables = {}
    variables = {str(k): _plain(v) for k, v in variables.items()}

    variable_meta_raw = data.get("variable_meta") or {}
    variable_meta: dict[str, dict[str, Any]] = {}
    if isinstance(variable_meta_raw, dict):
        for k, v in variable_meta_raw.items():
            if isinstance(v, dict):
                variable_meta[str(k)] = {str(kk): _plain(vv) for kk, vv in v.items()}

    run_configs_raw = data.get("run_configs") or []
    run_configs: list[dict[str, Any]] = []
    if isinstance(run_configs_raw, list):
        for entry in run_configs_raw:
            if isinstance(entry, dict):
                run_configs.append({str(k): _plain(v) for k, v in entry.items()})

    deps_raw = data.get("launch_dependencies") or []
    launch_dependencies: list[dict[str, Any]] = []
    if isinstance(deps_raw, list):
        for entry in deps_raw:
            if isinstance(entry, dict):
                launch_dependencies.append(
                    {str(k): _plain(v) for k, v in entry.items()}
                )

    return Project(
        folder=folder,
        name=str(data.get("name", "") or ""),
        description=str(data.get("description", "") or ""),
        template=str(data.get("template", "") or ""),
        kind=str(data.get("kind", TOUCHDESIGNER_KIND) or TOUCHDESIGNER_KIND),
        kind_data=kind_data,
        created=str(data.get("created", "") or ""),
        last_opened=str(data.get("last_opened", "") or ""),
        tags=[str(t) for t in tags],
        launch_on_startup=bool(data.get("launch_on_startup", False)),
        reopen_on_crash=bool(data.get("reopen_on_crash", False)),
        freeze_timeout_seconds=_int(data.get("freeze_timeout_seconds"), 30),
        startup_grace_seconds=_int(data.get("startup_grace_seconds"), 60),
        relaunch_delay_seconds=_int(data.get("relaunch_delay_seconds"), 5),
        max_retries_enabled=bool(data.get("max_retries_enabled", False)),
        max_retries=_int(data.get("max_retries"), 3),
        output_mode=str(data.get("output_mode", "") or ""),
        variables=variables,
        variable_meta=variable_meta,
        run_configs=run_configs,
        launch_dependencies=launch_dependencies,
        _raw=raw,
    )


def _to_dict(project: Project) -> dict[str, Any]:
    """Launcher-owned top-level fields, for merging into the on-disk map."""
    return {
        "name": project.name,
        "description": project.description,
        "template": project.template,
        "kind": project.kind,
        "created": project.created,
        "last_opened": project.last_opened,
        "tags": list(project.tags),
        "launch_on_startup": bool(project.launch_on_startup),
        "reopen_on_crash": bool(project.reopen_on_crash),
        "freeze_timeout_seconds": int(project.freeze_timeout_seconds),
        "startup_grace_seconds": int(project.startup_grace_seconds),
        "relaunch_delay_seconds": int(project.relaunch_delay_seconds),
        "max_retries_enabled": bool(project.max_retries_enabled),
        "max_retries": int(project.max_retries),
        "output_mode": str(project.output_mode or ""),
        "variables": dict(project.variables),
        "variable_meta": {k: dict(v) for k, v in project.variable_meta.items()},
        "run_configs": [dict(c) for c in project.run_configs],
        "launch_dependencies": [dict(d) for d in project.launch_dependencies],
    }


def _int(value: Any, default: int) -> int:
    """`int(value)`, with the same "falsy means default" rule as before, and
    the default instead of an exception for junk like ``"abc"``."""
    try:
        return int(value) if value else default
    except (TypeError, ValueError):
        return default


# Upper bound on nodes `_plain` will copy. A real alpacka.yml has a few
# hundred; YAML aliases can make a tiny file expand exponentially.
_PLAIN_NODE_LIMIT = 100_000


def _plain(value: Any) -> Any:
    """Convert ruamel CommentedMap/CommentedSeq to plain dict/list recursively.

    Preserves scalars as-is. Used when copying ruamel-loaded data into
    launcher-owned fields, so downstream code doesn't need to know about
    ruamel types. Raises ValueError on a recursive alias or an alias
    expansion that blows past `_PLAIN_NODE_LIMIT`.
    """
    budget = [_PLAIN_NODE_LIMIT]
    return _plain_inner(value, set(), budget)


def _plain_inner(value: Any, active: set[int], budget: list[int]) -> Any:
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError("alpacka.yml expands to too many values")
    if isinstance(value, (dict, list)):
        vid = id(value)
        if vid in active:
            raise ValueError("alpacka.yml contains a recursive alias")
        active.add(vid)
        try:
            if isinstance(value, dict):
                return {str(k): _plain_inner(v, active, budget) for k, v in value.items()}
            return [_plain_inner(v, active, budget) for v in value]
        finally:
            active.discard(vid)
    return value
