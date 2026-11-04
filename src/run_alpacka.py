"""PyInstaller entry point.

Inside the ``alpacka`` package every import is relative (docs/development.md), which
means the package's own ``__main__.py`` can't be used as the frozen
entry: PyInstaller runs it as ``__main__`` with no parent package, and
``from .app import run`` fails with "attempted relative import with no
known parent package".

This shim lives *outside* the package so it can use an absolute import
to reach in, mirroring what setuptools does for the ``alpacka`` console
script entry point (``alpacka = "alpacka.__main__:main"``).
"""
from __future__ import annotations

import sys

from alpacka.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
