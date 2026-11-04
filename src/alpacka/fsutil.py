"""Small filesystem helpers shared across modules (stdlib only, Qt-free)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_text_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` (UTF-8, no BOM, newlines as given) atomically.

    The temp file gets a fresh, unpredictable name in the same folder
    (`mkstemp`, created exclusively), then replaces `path`. A fixed name
    like ``alpacka.yml.tmp`` could already exist as a symlink — committed
    into a cloned project — and opening it would write through the link
    to any file the user can write.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
