"""Kind registry — dispatch point for per-kind launch / tracking / detect.

Handlers self-register at import time. `launch.py` and `process_tracker.py`
look up the handler via `get(kind_name)` to route kind-specific behavior
without knowing about any specific kind.
"""
from __future__ import annotations

from pathlib import Path

from .base import DetectResult, KindHandler, LaunchSpec, RunConfig

__all__ = [
    "DetectResult",
    "KindHandler",
    "LaunchSpec",
    "RunConfig",
    "register",
    "get",
    "all_handlers",
    "default_kind",
    "detect_kind",
]


REGISTRY: dict[str, KindHandler] = {}


def register(handler: KindHandler) -> None:
    REGISTRY[handler.name] = handler


def get(name: str) -> KindHandler:
    """Return the handler for `name`. Falls back to touchdesigner if unknown."""
    if name in REGISTRY:
        return REGISTRY[name]
    # Defensive fallback — an unknown kind in an alpacka.yml (maybe written
    # by a newer launcher version) should not crash older installs.
    return REGISTRY["touchdesigner"]


def all_handlers() -> list[KindHandler]:
    return list(REGISTRY.values())


def default_kind() -> str:
    return "touchdesigner"


def detect_kind(folder: Path) -> list[tuple[str, DetectResult]]:
    """Run every handler's detect() on `folder`, sorted by confidence desc.

    Filters out zero-confidence results (like executable's explicit-opt-in)
    unless nothing else scored — in which case we surface generic's fallback
    so the UI always has at least one option.
    """
    scored: list[tuple[str, DetectResult]] = []
    for handler in REGISTRY.values():
        try:
            result = handler.detect(folder)
        except Exception:  # noqa: BLE001
            continue
        if result.confidence > 0:
            scored.append((handler.name, result))
    scored.sort(key=lambda x: x[1].confidence, reverse=True)
    return scored


# Import kinds at package load — each module's bottom registers its HANDLER.
from . import touchdesigner as _td  # noqa: E402
from . import nodejs as _nodejs  # noqa: E402
from . import python as _python  # noqa: E402
from . import docker as _docker  # noqa: E402
from . import executable as _executable  # noqa: E402
from . import shell as _shell  # noqa: E402
from . import generic as _generic  # noqa: E402
from . import dependency as _dependency  # noqa: E402

register(_td.HANDLER)
register(_nodejs.HANDLER)
register(_python.HANDLER)
register(_docker.HANDLER)
register(_executable.HANDLER)
register(_shell.HANDLER)
register(_generic.HANDLER)
register(_dependency.HANDLER)
