"""External-CLI scaffolder for bundled templates.

Templates with a `scaffold:` block (only honored for bundled templates,
see `template_apply._load_config(..., trusted=True)`) invoke an external
command to generate the project tree — `npx sv create`, `npm create vite`,
`cookiecutter`, `dotnet new`, etc.

This module handles:
  - **Preflight** — check the required command (`requires_command`) is
    on PATH before we promise the user anything happens.
  - **Argv construction** — substitute ``{VAR}`` tokens in the command
    string from the user's collected `inputs` values, shlex-split, and
    drop any ``--flag ""`` pairs so empty multi-selects don't confuse
    the external CLI.
  - **Execution + streaming** (commit 5) — a threaded subprocess runner
    that streams stdout/stderr line-by-line and supports cancellation.

Multi-select values substitute as comma-joined strings
(``"prettier,eslint"``). Empty multi-select substitutes as the empty
string and the ``--flag ""`` cleanup drops the whole pair from argv.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .template_apply import ScaffoldSpec


@dataclass(frozen=True)
class PreflightFailure:
    """Why a scaffold can't run right now."""

    requires_command: str
    message: str


def check_preflight(spec: "ScaffoldSpec") -> PreflightFailure | None:
    """Return a PreflightFailure if the required command isn't on PATH.

    `spec.requires_command` is optional — if empty, we skip the check.
    """
    req = (spec.requires_command or "").strip()
    if not req:
        return None
    if shutil.which(req) is not None:
        return None
    return PreflightFailure(
        requires_command=req,
        message=(
            f"`{req}` is not on PATH. Install it (and its parent toolchain) "
            "before creating a project from this template."
        ),
    )


def build_argv(spec: "ScaffoldSpec", values: dict[str, Any]) -> list[str]:
    """Substitute values into `spec.command`, split, and clean.

    Approach:
      1. Shlex-split the raw command first — this preserves the token
         boundaries the template author intended (``--add`` +
         ``{SVELTE_ADDONS}`` are two separate tokens).
      2. For each token, substitute its ``{VAR}`` placeholders. A
         token that's a *pure reference* (``{VAR}`` alone with no
         surrounding text) whose value is a list expands to multiple
         argv tokens — one per list element. Other tokens substitute
         inline like a normal string template.
      3. Drop empty-substitution tokens, and drop the preceding token
         too if it looks like a flag. This cleanly handles the
         ``--add {EMPTY}`` case: both the flag and its placeholder
         disappear, so the scaffolder never sees a stray empty value
         or a dangling flag pointing at the next flag.

    Multi-select lists used as pure references expand to N argv
    tokens (matching scaffolders like ``sv create --add prettier
    eslint vitest``). Lists used inline (``--features={LIST}``) still
    comma-join for compat. Bool values become ``"1"`` / ``"0"``.
    Unknown ``{VAR}`` names are left literal.
    """
    raw_tokens = shlex.split(spec.command, posix=True)
    rendered: list[tuple[list[str], bool]] = []  # (tokens, became_empty)
    for token in raw_tokens:
        subbed, became_empty = _substitute_token(token, values)
        rendered.append((subbed, became_empty))

    cleaned: list[str] = []
    for tokens, became_empty in rendered:
        if became_empty:
            # Drop this token. If the previous kept token was a flag
            # waiting for a value, drop it too.
            if cleaned and _looks_like_flag(cleaned[-1]):
                cleaned.pop()
            continue
        cleaned.extend(tokens)
    return cleaned


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_TOKEN_RE = None  # lazy-compiled
_PURE_REF_RE = None  # lazy-compiled


def _substitute_token(
    token: str, values: dict[str, Any]
) -> tuple[list[str], bool]:
    """Substitute `{VAR}` refs in a single argv token.

    Returns `(tokens, became_empty)` — usually a single-element list,
    but a *pure-reference* token (``{VAR}`` alone, no surrounding text)
    whose value is a list/tuple/set expands to N tokens (one per
    non-empty element). Empty list → ``([], True)`` so build_argv
    drops the placeholder + its preceding flag.

    `became_empty` is True when the token contained at least one
    `{VAR}` ref whose value was empty AND the resulting token is
    entirely empty — so a plain ``{VAR}`` that's blank is dropped,
    but a token like ``--port={PORT}`` where PORT="" yields
    ``"--port="`` and is kept (the caller picks a different pattern
    if they want to omit the whole flag).
    """
    import re
    global _TOKEN_RE, _PURE_REF_RE
    if _TOKEN_RE is None:
        _TOKEN_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
    if _PURE_REF_RE is None:
        _PURE_REF_RE = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")

    # Pure reference path: the whole token is `{VAR}` and the value is
    # a list — expand each element to its own argv token. This is what
    # `sv create --add prettier eslint vitest` and similar variadic
    # CLIs need; the older comma-join behavior would have produced
    # `--add prettier,eslint,vitest` which most CLIs reject.
    pure = _PURE_REF_RE.match(token)
    if pure is not None:
        name = pure.group(1)
        if name in values:
            value = values[name]
            if isinstance(value, (list, tuple, set)):
                items = [str(v) for v in value if str(v) != ""]
                return items, len(items) == 0
            s = _scalarize(value)
            if s == "":
                return [], True
            return [s], False

    # Inline path — preserves old behavior (comma-join lists, scalarize
    # everything else, mark empty when the whole token collapses).
    had_empty_ref = False

    def repl(match: "re.Match[str]") -> str:
        nonlocal had_empty_ref
        name = match.group(1)
        if name not in values:
            return match.group(0)
        s = _scalarize(values[name])
        if s == "":
            had_empty_ref = True
        return s

    subbed = _TOKEN_RE.sub(repl, token)
    became_empty = had_empty_ref and subbed == ""
    return [subbed], became_empty


def _scalarize(value: Any) -> str:
    """Coerce a variable value to a single shell token string.

    Lists/tuples/sets comma-join only on the inline-substitution path;
    pure-reference tokens get the multi-token expansion in
    `_substitute_token`, so a list value never lands here on that path.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(v) for v in value if str(v))
    return str(value)


def _looks_like_flag(token: str) -> bool:
    """True for short and long option flags; False for positional tokens."""
    if not token:
        return False
    if token.startswith("--"):
        return True
    if token.startswith("-") and len(token) > 1 and not token[1].isdigit():
        return True
    return False


# ---------------------------------------------------------------------------
# Subprocess runner — blocking, streams output line-by-line
# ---------------------------------------------------------------------------


@dataclass
class CancelFlag:
    """Mutable cancellation token for the subprocess runner.

    Set `cancelled = True` from any thread; the runner polls it between
    reads and terminates the subprocess when it flips.
    """

    cancelled: bool = False


def run_subprocess(
    argv: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
    on_output: Callable[[str], None] | None = None,
    cancel: CancelFlag | None = None,
    *,
    timeout: float | None = None,
) -> int:
    """Spawn `argv`, stream stdout + stderr line-by-line, return exit code.

    Blocking — callers that need UI responsiveness run this on a worker
    thread (see `ScaffoldWorker` in the progress-dialog module).

    - `on_output(line)` receives each line as it arrives (trailing newlines
      stripped). stderr is merged into stdout so interleaving matches
      what the user would see in a terminal.
    - `cancel` is polled in a background thread that watches the flag
      and terminates the subprocess when set. Returns code `-15` (SIGTERM)
      or `-9` (SIGKILL) on cancellation, matching psutil's convention.
    - `timeout` kills after that many seconds (uncommon for scaffolders;
      None = wait forever).
    """
    effective_env = {**os.environ, **(env or {})}
    cancel = cancel or CancelFlag()

    # Resolve argv[0] via PATH before handing it to Popen. On Windows
    # the install entry points for Node tooling — `npm`, `npx`, `pnpm`,
    # `yarn`, `vite` — are `.cmd` shims, not `.exe`s. CreateProcess
    # without shell=True only matches `.exe` for bare names, so it
    # fails with WinError 2 ("system cannot find the file specified")
    # even though `shutil.which` happily finds the `.cmd`. Resolving
    # to the full path includes the right extension and dodges the
    # issue cross-platform without needing shell=True (which has its
    # own quoting hazards).
    if argv:
        resolved = shutil.which(argv[0])
        if resolved is not None:
            argv = [resolved, *argv[1:]]

    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=effective_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # line-buffered
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, OSError) as exc:
        if on_output is not None:
            on_output(f"[alpacka] failed to spawn: {exc}")
        return 127

    # Background watcher that terminates the child when the flag trips.
    def _watch_cancel() -> None:
        while proc.poll() is None:
            if cancel.cancelled:
                try:
                    proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
                # Give it a moment, then escalate.
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
                return
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                continue

    watcher = threading.Thread(target=_watch_cancel, daemon=True)
    watcher.start()

    # Stream output until EOF.
    try:
        if proc.stdout is not None:
            for line in proc.stdout:
                if on_output is not None:
                    on_output(line.rstrip("\r\n"))
    except Exception as exc:  # noqa: BLE001
        if on_output is not None:
            on_output(f"[alpacka] read error: {exc}")

    try:
        if timeout is not None:
            exit_code = proc.wait(timeout=timeout)
        else:
            exit_code = proc.wait()
    except subprocess.TimeoutExpired:
        proc.kill()
        exit_code = proc.wait()
    return exit_code
