"""Manage a curated set of TouchDesigner preferences in TD's `pref.txt`.

TD keeps its preferences in one machine-wide file per major version
(`%LOCALAPPDATA%/Derivative/TouchDesigner<major>/pref.txt` on Windows),
one ``key<TAB>value`` per line. Every TD build and every project on the
machine shares it, so ALPACKA never replaces the file. It only sets the
keys the user accepted (``managed``), leaves every other line alone and
in order, and can put each key back the way it found it.

Ownership:

- **Decisions** (``managed`` values, ``declined`` keys) are the user's
  choices from the first-run prompt / Settings. They are global, so a
  portable ALPACKA folder carries them to another machine.
- **Per-file bookkeeping** (``originals``, ``written``) is keyed by
  ``<hostname>|<pref.txt path>``, because the same path string on a
  different machine is a different file with different original values.

`reconcile()` is the only function that writes `pref.txt`. It runs just
before every TD launch (`launch._spawn`) and when the user clicks Apply /
Revert in Settings. It never writes while any TouchDesigner process is
running (a running TD rewrites the whole file from memory when its
Preferences dialog closes, undoing the write) and never raises: failures
become an ``error`` result.

Stdlib + `paths` only, Qt-free — like `template_apply.py`.
"""
from __future__ import annotations

import json
import math
import os
import platform
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from . import fsutil, paths

# ---------------------------------------------------------------------------
# Curated preference list
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrefSpec:
    key: str
    label: str
    description: str
    # Value ALPACKA sets. ``None`` means the UI supplies it (e.g. the
    # detected VS Code path for `dats.texteditor`).
    value: str | None
    kind: str = "bool"  # "bool" | "menu" | "path" | "str"
    # Ticked by default in the first-run prompt.
    default_checked: bool = False
    # TD's own default, i.e. what an absent key means. TD drops a line as
    # soon as its value is back at the default, so this must be right: a
    # wrong default makes every launch read the missing line as a change
    # made outside ALPACKA and write it again. ``None`` = unknown.
    td_default: str | None = None
    # ``kind == "menu"`` only: (stored value, label in TD's dialog).
    choices: tuple[tuple[str, str], ...] = ()


# Labels match TD's Preferences dialog. Values and defaults were checked in
# TD 2025.33070 by changing each preference in the dialog and diffing
# pref.txt: the default is the setting that makes TD remove the line.
RECOMMENDED: tuple[PrefSpec, ...] = (
    PrefSpec(
        key="general.stopplayingwhenminimized",
        label="Stop Playing when Minimized",
        description="Keep the timeline running when the TD window is minimized.",
        value="0",
        default_checked=True,
        td_default="1",
    ),
    PrefSpec(
        key="general.playbarshortcuts",
        label="Enable Playbar Shortcuts",
        description="Turn off playbar keyboard shortcuts so a stray key can't stop the timeline.",
        value="0",
        default_checked=True,
        td_default="1",
    ),
    PrefSpec(
        key="general.showstartuperrors",
        label="Show Startup Errors",
        description=(
            "None is TD's default. Keeps a warnings or errors popup off an "
            "unattended screen at startup."
        ),
        value="0",
        kind="menu",
        default_checked=True,
        td_default="0",
        choices=(("0", "None"), ("1", "Warnings"), ("2", "Errors")),
    ),
    PrefSpec(
        key="general.sounds",
        label="Enable Warning Sounds",
        description="Turn off TD's warning beeps so they don't play through the show's audio output.",
        value="0",
        default_checked=True,
        td_default="1",
    ),
    PrefSpec(
        key="general.saveprompt",
        label="Prompt to Save on Exit",
        description=(
            "A save prompt can hold up a scheduled restart. Turning it off means "
            "unsaved changes are lost when TD closes."
        ),
        value="0",
        default_checked=False,
        td_default="1",
    ),
    PrefSpec(
        key="general.hidesplashscreen",
        label="Hide Splash Screen",
        description=(
            "Keep TD's splash screen off the projector while it starts. Only "
            "works with a Commercial or Pro licence."
        ),
        value="1",
        default_checked=False,
        td_default="0",
    ),
    PrefSpec(
        key="dats.texteditor",
        label="DAT text editor",
        description="Edit DATs in VS Code. A project's own TOUCH_EDITOR setting may override this.",
        value=None,
        kind="path",
        default_checked=False,
        td_default="",
    ),
)

SPECS: dict[str, PrefSpec] = {s.key: s for s in RECOMMENDED}

# Never written by ALPACKA. Projects get Python packages from a per-project
# venv (tdPyEnvManager); pointing every TD at one system Python causes
# version conflicts. `pythonpackages64` is surfaced as a warning.
WATCH_ONLY: frozenset[str] = frozenset(
    {
        "general.pythonpackages64",
        "general.pythonusersitepackages",
        "general.pythonautopath",
        "general.pythonappendpath",
    }
)
PYTHON_PATH_KEY = "general.pythonpackages64"

BACKUP_SUFFIX = ".alpacka-backup"
STATE_VERSION = 1

# ---------------------------------------------------------------------------
# pref.txt location + I/O
# ---------------------------------------------------------------------------


def pref_path(major: str) -> Path | None:
    """`pref.txt` for a TD major version (e.g. ``"099"``), or None.

    Windows is verified. The macOS path comes from the old acro-td-core
    setup script and is unverified, so it is only used once TD has
    created the folder. Other platforms are unsupported.
    """
    folder = f"TouchDesigner{major}"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Derivative" / folder / "pref.txt"
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / "Derivative" / folder
        return root / "pref.txt" if root.is_dir() else None
    return None


def read_lines(path: Path) -> list[str]:
    """File lines without line endings. Tolerates a BOM; missing file → []."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return []
    return text.splitlines()


def _split(line: str) -> tuple[str, str] | None:
    """``key<TAB>value`` → (key, value), splitting on the first tab only."""
    if "\t" not in line:
        return None
    key, value = line.split("\t", 1)
    key = key.strip()
    return (key, value) if key else None


def parse_values(lines: Iterable[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        kv = _split(line)
        if kv is not None:
            values[kv[0]] = kv[1]
    return values


def read_values(path: Path) -> dict[str, str]:
    return parse_values(read_lines(path))


def merge(
    lines: list[str], set_keys: dict[str, str], remove_keys: Iterable[str] = ()
) -> list[str]:
    """Return `lines` with `set_keys` updated/appended and `remove_keys` dropped.

    Every other line is kept verbatim and in order. New keys go at the end.
    """
    remove = set(remove_keys)
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        kv = _split(line)
        if kv is None:
            out.append(line)
            continue
        key = kv[0]
        if key in remove:
            continue
        if key in set_keys:
            out.append(f"{key}\t{set_keys[key]}")
            seen.add(key)
            continue
        out.append(line)
    for key, value in set_keys.items():
        if key not in seen:
            out.append(f"{key}\t{value}")
    return out


def write_atomic(path: Path, lines: list[str]) -> None:
    """UTF-8 (no BOM), LF endings, via a temp file + replace."""
    text = "\n".join(lines) + ("\n" if lines else "")
    fsutil.write_text_atomic(path, text)


def backup_path(path: Path) -> Path:
    return path.with_name(path.name + BACKUP_SUFFIX)


def backup_once(path: Path) -> Path | None:
    """Copy `pref.txt` aside the first time ALPACKA changes it."""
    target = backup_path(path)
    if target.exists() or not path.is_file():
        return None
    shutil.copy2(path, target)
    return target


# ---------------------------------------------------------------------------
# Value comparison + display
# ---------------------------------------------------------------------------


def normalize(value: str, kind: str) -> str:
    """Form ALPACKA writes. TD stores paths with forward slashes."""
    value = value.strip()
    if kind == "path" and value:
        return value.replace("\\", "/")
    return value


def values_equal(a: str | None, b: str | None, kind: str = "str") -> bool:
    if a is None or b is None:
        return a is None and b is None
    a, b = a.strip(), b.strip()
    if a == b:
        return True
    if kind == "path":
        na, nb = a.replace("\\", "/"), b.replace("\\", "/")
        if sys.platform in ("win32", "darwin"):
            return na.casefold() == nb.casefold()
        return na == nb
    # TD rewrites floats with float32 noise (0.2 → 0.19999999).
    try:
        fa, fb = float(a), float(b)
    except ValueError:
        return False
    return math.isclose(fa, fb, rel_tol=1e-5, abs_tol=1e-6)


def _kind(key: str) -> str:
    spec = SPECS.get(key)
    return spec.kind if spec else "str"


def effective_value(key: str, current: dict[str, str]) -> str | None:
    """Value TD will use: the file's line, else TD's default if known."""
    if key in current:
        return current[key]
    spec = SPECS.get(key)
    return spec.td_default if spec else None


def display_value(key: str, value: str | None) -> str:
    """Human-readable value. ``None`` (key absent) shows TD's default."""
    spec = SPECS.get(key)
    kind = spec.kind if spec else "str"
    if value is None:
        if spec is not None and spec.td_default is not None:
            return f"{display_value(key, spec.td_default)} (TD default)"
        return "TD default"
    if kind == "bool":
        return {"0": "Off", "1": "On"}.get(value.strip(), value)
    if kind == "menu" and spec is not None:
        return dict(spec.choices).get(value.strip(), value)
    if kind == "path":
        return value if value.strip() else "not set"
    return value


def label_for(key: str) -> str:
    spec = SPECS.get(key)
    return spec.label if spec else key


# ---------------------------------------------------------------------------
# State (`td_prefs.json`)
# ---------------------------------------------------------------------------


@dataclass
class FileState:
    # Value before ALPACKA first changed the key in this file. ``None`` = the
    # key wasn't in the file. Presence here means ALPACKA owns the restore.
    originals: dict[str, str | None] = field(default_factory=dict)
    # What ALPACKA last wrote (or found already matching). A different
    # value later means someone changed it outside ALPACKA (drift).
    written: dict[str, str] = field(default_factory=dict)


@dataclass
class Change:
    key: str
    before: str | None
    after: str | None  # None = line removed
    drift: bool = False
    restore: bool = False

    def describe(self) -> str:
        before = display_value(self.key, self.before)
        after = display_value(self.key, self.after)
        return f"{label_for(self.key)}: {before} → {after}"


@dataclass
class Result:
    status: str  # "applied" | "up_to_date" | "waiting" | "error" | "unsupported"
    source: str = "launch"  # "launch" | "ui"
    path: str = ""
    at: str = ""
    changes: list[Change] = field(default_factory=list)
    message: str = ""

    @property
    def drift(self) -> list[Change]:
        return [c for c in self.changes if c.drift]


@dataclass
class State:
    managed: dict[str, str] = field(default_factory=dict)
    declined: list[str] = field(default_factory=list)
    files: dict[str, FileState] = field(default_factory=dict)
    last_result: Result | None = None

    def file_state(self, path: Path) -> FileState:
        key = _file_key(path)
        if key not in self.files:
            self.files[key] = FileState()
        return self.files[key]


def _file_key(path: Path) -> str:
    return f"{platform.node()}|{Path(path).as_posix()}"


def peek_file_state(state: State, path: Path) -> FileState:
    """Bookkeeping for `path` on this machine, without adding it to `state`."""
    return state.files.get(_file_key(path)) or FileState()


def state_path() -> Path:
    return paths.config_root() / "td_prefs.json"


def load_state() -> State:
    path = state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return State()
    if not isinstance(data, dict):
        return State()
    files = {
        str(k): FileState(
            originals=dict(v.get("originals") or {}),
            written={str(a): str(b) for a, b in (v.get("written") or {}).items()},
        )
        for k, v in (data.get("files") or {}).items()
        if isinstance(v, dict)
    }
    last = data.get("last_result")
    last_result = None
    if isinstance(last, dict):
        try:
            last_result = Result(
                status=str(last.get("status", "")),
                source=str(last.get("source", "launch")),
                path=str(last.get("path", "")),
                at=str(last.get("at", "")),
                changes=[Change(**c) for c in last.get("changes") or []],
                message=str(last.get("message", "")),
            )
        except TypeError:
            last_result = None
    return State(
        managed={str(k): str(v) for k, v in (data.get("managed") or {}).items()},
        declined=[str(k) for k in data.get("declined") or []],
        files=files,
        last_result=last_result,
    )


def save_state(state: State) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": STATE_VERSION,
        "managed": state.managed,
        "declined": sorted(set(state.declined)),
        "files": {k: asdict(v) for k, v in state.files.items()},
        "last_result": asdict(state.last_result) if state.last_result else None,
    }
    fsutil.write_text_atomic(path, json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Decisions (first-run prompt + Settings)
# ---------------------------------------------------------------------------


def undecided_specs(state: State) -> list[PrefSpec]:
    """Recommended keys the user hasn't accepted or declined yet."""
    declined = set(state.declined)
    return [s for s in RECOMMENDED if s.key not in state.managed and s.key not in declined]


def decide(accept: dict[str, str], decline: Iterable[str]) -> State:
    """Record the user's choices. Accepted keys become managed."""
    state = load_state()
    declined = set(state.declined)
    for key, value in accept.items():
        value = normalize(value or "", _kind(key))
        if key in WATCH_ONLY or not value:
            continue
        state.managed[key] = value
        declined.discard(key)
    for key in decline:
        state.managed.pop(key, None)
        declined.add(key)
    state.declined = sorted(declined)
    save_state(state)
    return state


def revert_all() -> State:
    """Stop managing every key; the next reconcile restores originals."""
    state = load_state()
    state.declined = sorted(set(state.declined) | set(state.managed))
    state.managed = {}
    save_state(state)
    return state


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

_listeners: list[Callable[[Result], None]] = []


def subscribe(callback: Callable[[Result], None]) -> None:
    if callback not in _listeners:
        _listeners.append(callback)


def unsubscribe(callback: Callable[[Result], None]) -> None:
    if callback in _listeners:
        _listeners.remove(callback)


def _notify(result: Result) -> None:
    for cb in list(_listeners):
        try:
            cb(result)
        except Exception:  # noqa: BLE001 — a UI listener must never break a launch
            pass


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def plan_changes(
    state: State, fstate: FileState, current: dict[str, str]
) -> tuple[dict[str, str], set[str], list[Change], set[str]]:
    """Work out what `pref.txt` needs. Pure — no I/O.

    Returns ``(sets, removes, changes, observed)`` where ``observed`` are
    managed keys that already match (recorded as written for drift
    detection without a write).
    """
    sets: dict[str, str] = {}
    removes: set[str] = set()
    changes: list[Change] = []
    observed: set[str] = set()

    for key, value in state.managed.items():
        if key in WATCH_ONLY:
            continue
        kind = _kind(key)
        value = normalize(value, kind)
        effective = effective_value(key, current)
        if effective is not None and values_equal(effective, value, kind):
            observed.add(key)
            continue
        last = fstate.written.get(key)
        drift = last is not None and not values_equal(effective, last, kind)
        sets[key] = value
        changes.append(Change(key=key, before=current.get(key), after=value, drift=drift))

    # Keys ALPACKA changed earlier but no longer manages: put them back.
    for key, original in fstate.originals.items():
        if key in state.managed or key in WATCH_ONLY:
            continue
        kind = _kind(key)
        cur = current.get(key)
        if original is None:
            if cur is not None:
                removes.add(key)
                changes.append(Change(key=key, before=cur, after=None, restore=True))
        elif cur is None or not values_equal(cur, original, kind):
            sets[key] = original
            changes.append(Change(key=key, before=cur, after=original, restore=True))

    return sets, removes, changes, observed


def reconcile(major: str, *, td_running: bool, source: str = "launch") -> Result:
    """Bring `pref.txt` for `major` in line with the managed keys.

    Never raises. Writes only when something differs and no TD is running.
    """
    try:
        result = _reconcile(major, td_running=td_running, source=source)
    except Exception as exc:  # noqa: BLE001 — must never block a launch
        result = Result(status="error", source=source, at=_now(), message=str(exc))
        try:
            state = load_state()
            state.last_result = result
            save_state(state)
        except Exception:  # noqa: BLE001
            pass
    _notify(result)
    return result


def _reconcile(major: str, *, td_running: bool, source: str) -> Result:
    path = pref_path(major)
    if path is None:
        return Result(status="unsupported", source=source, at=_now())

    state = load_state()
    if not state.managed and not peek_file_state(state, path).originals:
        # Nothing opted in and nothing to restore: don't touch any file,
        # not even td_prefs.json, so Settings still reads "nothing changed".
        return Result(status="up_to_date", source=source, path=str(path), at=_now())
    fstate = state.file_state(path)
    lines = read_lines(path)
    current = parse_values(lines)
    sets, removes, changes, observed = plan_changes(state, fstate, current)

    for key in observed:
        fstate.written[key] = normalize(state.managed[key], _kind(key))
    # Forget drift bookkeeping for keys no longer managed.
    for key in list(fstate.written):
        if key not in state.managed:
            del fstate.written[key]
    # Restores that already match need no write; just drop the original.
    for key in list(fstate.originals):
        if key in state.managed:
            continue
        if not any(c.key == key for c in changes):
            del fstate.originals[key]

    result = Result(status="up_to_date", source=source, path=str(path), at=_now())
    if changes and td_running:
        result.status = "waiting"
        result.changes = changes
    elif changes:
        backup_once(path)
        for key in sets:
            if key in state.managed and key not in fstate.originals:
                fstate.originals[key] = current.get(key)
        write_atomic(path, merge(lines, sets, removes))
        for change in changes:
            if change.restore:
                fstate.originals.pop(change.key, None)
                fstate.written.pop(change.key, None)
            else:
                fstate.written[change.key] = sets[change.key]
        result.status = "applied"
        result.changes = changes

    state.last_result = result
    save_state(state)
    return result


def describe_result(result: Result) -> str:
    """One-paragraph summary for the drift banner / status tooltip."""
    if result.status == "error":
        return f"Couldn't update TouchDesigner preferences: {result.message}"
    drift = [c for c in result.changes if c.drift]
    other = [c for c in result.changes if not c.drift]
    parts: list[str] = []
    if drift:
        verb = "Set back" if result.status == "applied" else "Will set back at the next launch"
        parts.append(
            "TouchDesigner preferences were changed outside ALPACKA. "
            f"{verb}: " + "; ".join(c.describe() for c in drift) + "."
        )
    if other:
        verb = "ALPACKA set" if result.status == "applied" else "Waiting to set"
        parts.append(f"{verb} TouchDesigner preferences: " + "; ".join(c.describe() for c in other) + ".")
    return " ".join(parts)


def python_path_warning(current: dict[str, str]) -> str | None:
    value = (current.get(PYTHON_PATH_KEY) or "").strip()
    if not value:
        return None
    return (
        f"TouchDesigner's Python 64-bit Module Path is set to {value}. Packages there "
        "can override a project's own venv. ALPACKA doesn't change this; clear it in "
        "TD's Preferences if projects pick up the wrong package versions."
    )


def majors_for(installs: Iterable[object]) -> list[str]:
    """Distinct TD majors from `settings.td_installs`, default first."""
    seen: list[str] = []
    for inst in installs:
        major = str(getattr(inst, "major", "") or "")
        if major and major not in seen:
            seen.append(major)
    return seen
