"""Global launcher settings persisted to settings.json."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from . import fsutil, paths


@dataclass
class TDInstall:
    version: str  # e.g. "2025.32280"
    major: str  # e.g. "099"
    path: str  # absolute path to TouchDesigner.exe or the .app bundle


@dataclass
class Settings:
    td_installs: list[TDInstall] = field(default_factory=list)
    default_td_install: str = ""  # matches one of td_installs[].version
    theme: str = "dark"
    # Updater state — see `alpacka.updater`. `last_update_check` is an epoch
    # timestamp (0.0 = never). `skipped_version` suppresses the startup
    # prompt for that one version; it clears itself when a newer release
    # ships (only ever matched against the latest tag).
    auto_update_check: bool = True
    last_update_check: float = 0.0
    skipped_version: str = ""
    # Width of the sidebar (tree + nav) in pixels. Persisted so the
    # user's preferred width survives restarts.
    sidebar_width: int = 180
    # Library card density — "expanded" (default, thumbnail + description)
    # or "compact" (no thumbnail/description, tighter height). Persisted.
    card_density: str = "expanded"
    # Default parent folder pre-filled in the New Project dialog. Empty
    # string = no default (user picks per-project). Stored verbatim;
    # users with non-existent paths see them surfaced in Settings so
    # they can fix them rather than having creates fail mysteriously.
    default_parent_folder: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "td_installs": [asdict(i) for i in self.td_installs],
            "default_td_install": self.default_td_install,
            "theme": self.theme,
            "auto_update_check": self.auto_update_check,
            "last_update_check": self.last_update_check,
            "skipped_version": self.skipped_version,
            "sidebar_width": self.sidebar_width,
            "card_density": self.card_density,
            "default_parent_folder": self.default_parent_folder,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        installs = [TDInstall(**i) for i in data.get("td_installs", [])]
        density = str(data.get("card_density", "expanded"))
        if density not in ("expanded", "compact"):
            density = "expanded"
        return cls(
            td_installs=installs,
            default_td_install=data.get("default_td_install", ""),
            theme=data.get("theme", "dark"),
            auto_update_check=bool(data.get("auto_update_check", True)),
            last_update_check=float(data.get("last_update_check", 0.0)),
            skipped_version=str(data.get("skipped_version", "")),
            sidebar_width=int(data.get("sidebar_width", 180)),
            card_density=density,
            default_parent_folder=str(data.get("default_parent_folder", "") or ""),
        )


def load() -> Settings:
    path = paths.settings_path()
    if not path.exists():
        return Settings()
    try:
        with path.open("r", encoding="utf-8") as f:
            return Settings.from_dict(json.load(f))
    except (json.JSONDecodeError, TypeError, ValueError):
        return Settings()


def save(settings: Settings) -> None:
    paths.ensure_dirs()
    fsutil.write_text_atomic(
        paths.settings_path(), json.dumps(settings.to_dict(), indent=2)
    )
