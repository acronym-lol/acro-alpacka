"""Generated thumbnail placeholders for projects without a custom image.

When a project has no ``.launcher-thumb.png``, the card falls back to a
``QPixmap`` produced here: a hash-seeded gradient with the project's
initials centered and the kind's icon as a small badge in the corner.
The intent is something users can tell apart at a glance without having
to drop in an image first; a real thumbnail takes precedence the moment
one shows up at ``project.thumbnail_path``.

Results are memoized in-process — generation is cheap, but cards
rebuild every refresh and library views can hold dozens of rows.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
)

_ICONS_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"

# Color quartets painted onto all four corners of the tile, blended via
# stacked radial gradients. Tetradic-inspired layout — the primary hue
# family (h, h+30°) sits on the top-left↔bottom-right diagonal; the
# complement family (h+150°, h+180°) sits on the top-right↔bottom-left
# diagonal. That sets up a visible warm-cool tension across both
# diagonals, which uses the rectangular tile shape to its advantage
# rather than concentrating color in one corner.
#
# The base hue is hashed directly from the project name (mod 360°), so
# every project lands on its own palette. The other three corners are
# derived from it, so the relationships stay consistent across the wheel.

# Off-white that reads on every gradient in the palette without being
# blindingly white. Used for both the initials and the kind-icon strokes/fills.
_FOREGROUND_HEX = "#E8E8E8"

# Color baked into every vendored kind icon (both Lucide line icons via
# `stroke="#BEB7AF"` and Simple Icons brand glyphs via `fill="#BEB7AF"`).
# We swap it for `_FOREGROUND_HEX` at render time so badges read crisply
# on the placeholder gradient. Anchoring on this exact hex keeps inner
# accents (e.g. the dark rect inside `terminal.svg`) untouched.
_CHROME_COLOR = "#BEB7AF"

_CACHE: dict[tuple[str, str, int, int], QPixmap] = {}


def make_placeholder(
    name: str,
    kind_icon: str,
    width: int,
    height: int,
) -> QPixmap:
    """Generate a thumbnail placeholder QPixmap for a project.

    Layout: hash-seeded linear gradient, project initials centered,
    kind icon as a small badge bottom-right. Results memoized by
    ``(name, kind_icon, width, height)``.
    """
    key = (name, kind_icon, width, height)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    pixmap = _render(name, kind_icon, width, height)
    _CACHE[key] = pixmap
    return pixmap


def clear_cache() -> None:
    """Drop all cached placeholders. Mostly here for tests."""
    _CACHE.clear()


def _render(name: str, kind_icon: str, width: int, height: int) -> QPixmap:
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    _paint_corner_gradient(painter, name, width, height)

    initials = _initials_for(name)
    if initials:
        font = QFont(painter.font())
        font.setWeight(QFont.Weight.DemiBold)
        # Start at ~half the slot height (matches the 2-letter default
        # users got before this rule change). Shrink to fit horizontally
        # so longer initials like "TNP" / "BAR" / "LNWMP" don't overflow
        # — capped at ~85% of tile width for breathing room and to keep
        # clear of the kind-icon badge in the bottom-right.
        font.setPixelSize(max(14, height // 2))
        max_text_width = int(width * 0.85)
        metrics = QFontMetrics(font)
        text_width = metrics.horizontalAdvance(initials)
        if text_width > max_text_width and text_width > 0:
            scale = max_text_width / text_width
            font.setPixelSize(max(14, int(font.pixelSize() * scale)))
        painter.setFont(font)
        painter.setPen(QColor(_FOREGROUND_HEX))
        painter.drawText(
            QRectF(0.0, 0.0, float(width), float(height)),
            int(Qt.AlignmentFlag.AlignCenter),
            initials,
        )

    # Badge edge ≈ 3/16 of the shorter tile edge — three-quarters of the
    # quarter-tile sizing we used before, so the kind glyph reads as a
    # subtle corner badge rather than a competing focal point.
    icon_size = max(12, (min(width, height) * 3) // 16)
    margin = max(6, icon_size // 3)
    icon_rect = QRectF(
        float(width - icon_size - margin),
        float(height - icon_size - margin),
        float(icon_size),
        float(icon_size),
    )
    _draw_kind_icon(painter, kind_icon, icon_rect)

    painter.end()
    return pixmap


def _draw_kind_icon(
    painter: QPainter, kind_icon: str, rect: QRectF
) -> None:
    """Draw the kind icon badge with a soft drop shadow into `rect`.

    Both SVG (Lucide / Simple Icons) and PNG (TD brand mark) sources go
    through a single path: render to a transparent temp pixmap at the
    badge size, then composite via ``QGraphicsDropShadowEffect`` so the
    icon reads on any gradient — including ones where the icon's brand
    color is close to the project's hash-picked hue.
    """
    if not kind_icon:
        return
    target = int(rect.width())
    icon_pm = _render_icon_pixmap(kind_icon, target, target)
    if icon_pm is None:
        return
    shadowed, pad = _with_drop_shadow(icon_pm)
    # `_with_drop_shadow` pads the pixmap by `pad` on every side so the
    # blur isn't clipped. Offset the draw position by the same amount so
    # the icon itself still lands at `rect`'s top-left.
    painter.drawPixmap(int(rect.x()) - pad, int(rect.y()) - pad, shadowed)


def _render_icon_pixmap(kind_icon: str, w: int, h: int) -> QPixmap | None:
    """Render the kind icon into a transparent ``w×h`` pixmap.

    Tries ``<kind_icon>.svg`` first (recoloring chrome glyphs), then
    falls back to ``<kind_icon>.png`` (mip-style downsample for clean
    edges at any badge size). Returns ``None`` if no source exists or
    rendering fails.
    """
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    svg_path = _ICONS_DIR / f"{kind_icon}.svg"
    if svg_path.is_file():
        try:
            raw = svg_path.read_text(encoding="utf-8")
        except OSError:
            return None
        recolored = raw.replace(
            f'stroke="{_CHROME_COLOR}"', f'stroke="{_FOREGROUND_HEX}"'
        ).replace(
            f'fill="{_CHROME_COLOR}"', f'fill="{_FOREGROUND_HEX}"'
        )
        renderer = QSvgRenderer(recolored.encode("utf-8"))
        if not renderer.isValid():
            return None
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        renderer.render(p, QRectF(0.0, 0.0, float(w), float(h)))
        p.end()
        return pm
    png_path = _ICONS_DIR / f"{kind_icon}.png"
    if png_path.is_file():
        src = QPixmap(str(png_path))
        if src.isNull():
            return None
        scaled = _smooth_downscale(src, w)
        ox = (w - scaled.width()) // 2
        oy = (h - scaled.height()) // 2
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawPixmap(ox, oy, scaled)
        p.end()
        return pm
    return None


# Drop shadow params for kind-icon badges. Tuned to stay subtle while
# clearly lifting the badge off the gradient.
_SHADOW_BLUR = 8
_SHADOW_OFFSET = (0, 2)
_SHADOW_COLOR = QColor(0, 0, 0, 230)


def _with_drop_shadow(source: QPixmap) -> tuple[QPixmap, int]:
    """Return ``(padded_pixmap, pad)`` — the source rendered with a
    blurred drop shadow underneath. The result is larger than ``source``
    by ``pad`` pixels on every side so the blur isn't cropped at the
    edges; callers offset their draw position by ``pad`` to keep the
    source's top-left aligned with where they intended it.

    Used for the kind-icon badge inside the placeholder. The card-level
    thumbnail drop shadow uses ``setGraphicsEffect`` directly on the
    widget — that path doesn't need this helper because Qt manages the
    extra render pass.
    """
    pad = _SHADOW_BLUR + max(abs(_SHADOW_OFFSET[0]), abs(_SHADOW_OFFSET[1])) + 1

    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(source)
    effect = QGraphicsDropShadowEffect()
    effect.setBlurRadius(_SHADOW_BLUR)
    effect.setXOffset(_SHADOW_OFFSET[0])
    effect.setYOffset(_SHADOW_OFFSET[1])
    effect.setColor(_SHADOW_COLOR)
    item.setGraphicsEffect(effect)
    scene.addItem(item)

    out_w = source.width() + 2 * pad
    out_h = source.height() + 2 * pad
    result = QPixmap(out_w, out_h)
    result.fill(Qt.GlobalColor.transparent)
    p = QPainter(result)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    # Render the scene with the source positioned `pad` pixels inset so
    # there's room for the blur to spread on every side.
    scene.render(
        p,
        QRectF(0.0, 0.0, float(out_w), float(out_h)),
        QRectF(
            -float(pad),
            -float(pad),
            float(out_w),
            float(out_h),
        ),
    )
    p.end()
    return result, pad


def _smooth_downscale(pm: QPixmap, target: int) -> QPixmap:
    """Mipmap-style bilinear downsample to a square `target` edge.

    Qt's single-step ``drawPixmap`` with ``SmoothPixmapTransform`` does
    one bilinear pass, which aliases badly when the ratio is large
    (e.g. 256 → 37 = ~7×). Iteratively halving keeps each step at 2:1
    where bilinear is exact, then one final scale lands on the target.
    """
    if target <= 0 or pm.isNull():
        return pm
    current = pm
    while current.width() > target * 2 and current.width() > 1:
        current = current.scaled(
            current.width() // 2,
            current.height() // 2,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    if current.width() != target:
        current = current.scaled(
            target,
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return current


def _hash_index(value: str, modulo: int) -> int:
    """Stable cross-process hash → ``[0, modulo)`` index.

    SHA-1 (not Python's built-in ``hash``) so the same project name
    always lands on the same gradient across runs and across machines.
    """
    digest = hashlib.sha1(value.encode("utf-8", errors="replace")).digest()
    return int.from_bytes(digest[:4], "big") % modulo


def _gradient_for(name: str) -> tuple[str, str, str, str]:
    """Derive the four corner colors from a hash-seeded base hue."""
    base = _hash_index(name, 360)
    tl = QColor.fromHslF(base / 360.0, 0.55, 0.32).name()
    tr = QColor.fromHslF(((base + 150) % 360) / 360.0, 0.62, 0.32).name()
    br = QColor.fromHslF(((base + 30) % 360) / 360.0, 0.55, 0.30).name()
    bl = QColor.fromHslF(((base + 180) % 360) / 360.0, 0.62, 0.30).name()
    return (tl, tr, br, bl)


def _paint_corner_gradient(
    painter: QPainter, name: str, width: int, height: int
) -> None:
    """Four colors painted at the four corners and blended via radials.

    Base fill is the top-left color; the other three corners are
    overlaid as radial gradients fading to transparent. Each corner's
    color stays dominant near itself; the center receives a soft mix of
    all four. Alphas are tuned so the dominant TL never gets fully
    erased near the opposite corner — keeps the diagonal tension alive.
    """
    tl, tr, br, bl = _gradient_for(name)
    diag = float(max(width, height))

    painter.fillRect(0, 0, width, height, QColor(tl))

    # Top-right radial — complement family, tints upper-right.
    _overlay_radial(painter, tr, QPointF(float(width), 0.0), diag * 1.0, alpha=215, w=width, h=height)
    # Bottom-right radial — primary family (analogous), tints lower-right.
    _overlay_radial(painter, br, QPointF(float(width), float(height)), diag * 0.95, alpha=215, w=width, h=height)
    # Bottom-left radial — complement, tints lower-left.
    _overlay_radial(painter, bl, QPointF(0.0, float(height)), diag * 1.0, alpha=215, w=width, h=height)


def _overlay_radial(
    painter: QPainter,
    color_hex: str,
    center: QPointF,
    radius: float,
    *,
    alpha: int,
    w: int,
    h: int,
) -> None:
    full = QColor(color_hex)
    full.setAlpha(alpha)
    clear = QColor(color_hex)
    clear.setAlpha(0)
    grad = QRadialGradient(center, radius)
    grad.setColorAt(0.0, full)
    grad.setColorAt(1.0, clear)
    painter.fillRect(0, 0, w, h, grad)


def _initials_for(name: str) -> str:
    """Pick the centered glyph(s) from `name`.

    Splits on whitespace / hyphens / underscores. Multi-word names take
    the first alphanumeric of every word — so ``"Test none python"`` is
    ``"TNP"`` and ``"build-and-release"`` is ``"BAR"``. Single-word names
    keep their first two alphanumerics (``"alpacka"`` → ``"AL"``).
    """
    cleaned = name.strip()
    if not cleaned:
        return ""
    words = [w for w in re.split(r"[\s\-_]+", cleaned) if w]
    if len(words) >= 2:
        letters: list[str] = []
        for w in words:
            first = next((c for c in w if c.isalnum()), "")
            if first:
                letters.append(first)
        return "".join(letters).upper()
    alnums = [c for c in words[0] if c.isalnum()]
    return "".join(alnums[:2]).upper()
