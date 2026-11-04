"""Render the Lucide tag glyph tinted to a caller-supplied color.

Used by the sidebar tree and the content-area tag tiles so every tag
icon in the app picks up the tag's chosen color. The base tag shape is
embedded as an SVG string (rather than loaded from disk) so we can
substitute the stroke and fill colors without having to generate files.
Passing an empty ``color`` falls back to the default STEEL BLUE tint
used when no tag color has been set.
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# STEEL BLUE — matches the "no color" tint used elsewhere (chips, tile
# edge accent). Any color query falls back to this.
_DEFAULT_COLOR = "#7D93AF"

# How much darker the fill is relative to the stroke. ``QColor.darker``
# takes an integer factor where 200 ≈ 50% lightness. 240 reads as "same
# hue, clearly darker" without losing the color identity to near-black.
_FILL_DARKEN = 240

# Base SVG: Lucide tag glyph with placeholders for stroke and fill. The
# dot (``<circle>``) stays at the stroke color so it pops against the
# darker fill.
_SVG_TEMPLATE = """<svg xmlns="http://www.w3.org/2000/svg" \
width="{size}" height="{size}" viewBox="0 0 24 24" \
fill="{fill}" stroke="{stroke}" stroke-width="1.75" \
stroke-linecap="round" stroke-linejoin="round">
  <path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z"/>
  <circle cx="7.5" cy="7.5" r="1.1" fill="{stroke}" stroke="none"/>
</svg>"""


@lru_cache(maxsize=128)
def tag_icon(color_hex: str = "", size: int = 14) -> QIcon:
    """Return a filled Lucide tag icon tinted to ``color_hex``.

    ``color_hex`` accepts any ``QColor``-parseable string (``"#RRGGBB"``,
    named colors, etc.). Empty or invalid colors use the default tint.
    Results are memoized since these icons are rebuilt on every tree /
    tile redraw.
    """
    base = QColor(color_hex) if color_hex else QColor(_DEFAULT_COLOR)
    if not base.isValid():
        base = QColor(_DEFAULT_COLOR)

    stroke = base.name()
    fill = base.darker(_FILL_DARKEN).name()

    svg_bytes = _SVG_TEMPLATE.format(
        size=size, stroke=stroke, fill=fill
    ).encode("utf-8")
    renderer = QSvgRenderer(svg_bytes)
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return QIcon(pm)
