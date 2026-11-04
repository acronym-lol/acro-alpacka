"""Rasterize brand SVGs into every bundled asset the release needs.

Run from the project root via the venv Python:

    .venv/Scripts/python.exe scripts/generate_icons.py

Inputs:
    brand/ACRONYM_Logomark_White_RGB.svg   (source of truth for app icons)
    brand/ACRONYM_Logomark_Black_RGB.svg   (source of truth for installer art)
    src/alpacka/resources/icons/alpacka-logo.svg (runtime asset — used directly by
                                            the in-app title bar; we feed it
                                            as the icon source because it's
                                            already inlined with a visible
                                            fill for Qt's QIcon)

Outputs:
    src/alpacka/resources/icons/alpacka.ico
    src/alpacka/resources/icons/alpacka.icns
    installer/assets/wizard-banner-100.bmp, -150.bmp, -200.bmp
    installer/assets/wizard-header-100.bmp, -150.bmp, -200.bmp

The ICO is a multi-image PNG-based file assembled by hand so every target
size gets a hand-rendered bitmap (vs. one master downsampled). ICNS is
also hand-assembled with ic07-ic14 + icp4/icp5 chunks for modern macOS.
Wizard BMPs are 24-bit (``Format_RGB888``) so Inno Setup handles them
cleanly on every Windows version — no alpha-channel surprises.
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

# Offscreen keeps this headless-safe; Qt still needs a QGuiApplication.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "src" / "alpacka" / "resources" / "icons"
INSTALLER_ASSETS = ROOT / "installer" / "assets"
BRAND_DIR = ROOT / "brand"

ICON_SOURCE_SVG = ICON_DIR / "alpacka-logo.svg"       # white/pale fill, runtime-ready
WIZARD_SOURCE_SVG = BRAND_DIR / "ACRONYM_Logomark_Black_RGB.svg"

ICO_PATH = ICON_DIR / "alpacka.ico"
ICNS_PATH = ICON_DIR / "alpacka.icns"

WIN_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]

ICNS_CHUNKS: list[tuple[str, int]] = [
    ("icp4", 16),
    ("icp5", 32),
    ("ic07", 128),
    ("ic08", 256),
    ("ic09", 512),
    ("ic10", 1024),
    ("ic11", 32),    # 16@2x
    ("ic12", 64),    # 32@2x
    ("ic13", 256),   # 128@2x
    ("ic14", 512),   # 256@2x
]

# Inno Setup wizard image sizes. 100 is the base (96 DPI); 150/200 cover
# the two most common HiDPI steps. Inno picks the closest match at runtime.
BANNER_BASE = (164, 314)
HEADER_BASE = (55, 58)
WIZARD_DPI_SCALES: list[tuple[str, float]] = [
    ("100", 1.00),
    ("150", 1.50),
    ("200", 2.00),
]
# Fraction of the short edge the logo occupies. 0.65 keeps healthy margins
# on the banner (tall aspect) and still reads clearly on the small header.
_LOGO_FILL_RATIO = 0.65
# viewBox aspect of the logomark (263.45 / 250).
_LOGO_ASPECT = 263.45 / 250.0


def render_png_bytes(svg_path: Path, size: int) -> bytes:
    """Rasterize ``svg_path`` to a PNG buffer at ``size × size`` (transparent)."""
    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        raise RuntimeError(f"QSvgRenderer could not parse {svg_path}")

    qimg = QImage(size, size, QImage.Format.Format_ARGB32)
    qimg.fill(Qt.GlobalColor.transparent)
    painter = QPainter(qimg)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter)
    painter.end()

    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.ReadWrite)
    qimg.save(buf, "PNG")
    return bytes(buf.data())


def write_ico(svg_path: Path, out: Path, sizes: list[int]) -> None:
    """Assemble a multi-image PNG-based ICO from ``svg_path``."""
    pngs = [render_png_bytes(svg_path, s) for s in sizes]
    n = len(pngs)
    header = struct.pack("<HHH", 0, 1, n)

    entries = bytearray()
    payload = bytearray()
    offset = 6 + 16 * n
    for size, png in zip(sizes, pngs):
        w = size if size < 256 else 0
        h = size if size < 256 else 0
        entries += struct.pack(
            "<BBBBHHII",
            w, h, 0, 0, 1, 32, len(png), offset,
        )
        payload += png
        offset += len(png)

    out.write_bytes(bytes(header) + bytes(entries) + bytes(payload))


def write_icns(svg_path: Path, out: Path, chunks: list[tuple[str, int]]) -> None:
    """Assemble an ICNS file with PNG-encoded chunks."""
    pixel_sizes = sorted({size for _, size in chunks})
    rendered = {size: render_png_bytes(svg_path, size) for size in pixel_sizes}

    body = bytearray()
    for code, size in chunks:
        png = rendered[size]
        body += code.encode("ascii")
        body += struct.pack(">I", 8 + len(png))
        body += png

    total = 8 + len(body)
    out.write_bytes(b"icns" + struct.pack(">I", total) + bytes(body))


def render_wizard_bmp(
    svg_path: Path,
    out: Path,
    target_wh: tuple[int, int],
    *,
    logo_fill_ratio: float = _LOGO_FILL_RATIO,
    bg: Qt.GlobalColor = Qt.GlobalColor.white,
) -> None:
    """Render the logomark centered on a solid-color BMP.

    ``target_wh`` is the output pixel size. The logo fits inside a box
    sized to ``logo_fill_ratio * min(w, h)`` with aspect ratio preserved.

    Inno Setup 6 supports 24-bit BMPs reliably on all Windows versions
    (32-bit BMPs with alpha are accepted on modern Windows but some
    wizard pages render the alpha incorrectly). We render into an
    RGB32 image (the format QPainter prefers) then convert to RGB888
    at save time so the BMP header is 24-bit.
    """
    w, h = target_wh
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(bg)

    # Fit the logo inside a (logo_fill_ratio * short_edge)^2 box, preserving aspect.
    short_edge = min(w, h)
    box = logo_fill_ratio * short_edge
    if box / _LOGO_ASPECT <= box:
        logo_h = box / _LOGO_ASPECT if _LOGO_ASPECT >= 1 else box
        logo_w = box if _LOGO_ASPECT >= 1 else box * _LOGO_ASPECT
    else:
        logo_h = box
        logo_w = box * _LOGO_ASPECT
    x = (w - logo_w) / 2
    y = (h - logo_h) / 2

    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        raise RuntimeError(f"QSvgRenderer could not parse {svg_path}")
    renderer.render(painter, QRectF(x, y, logo_w, logo_h))
    painter.end()

    img_24 = img.convertToFormat(QImage.Format.Format_RGB888)
    if not img_24.save(str(out), "BMP"):
        raise RuntimeError(f"Failed to write {out}")


def main() -> int:
    missing = [p for p in (ICON_SOURCE_SVG, WIZARD_SOURCE_SVG) if not p.is_file()]
    if missing:
        for p in missing:
            print(f"missing source SVG: {p}", file=sys.stderr)
        return 1

    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    _ = app  # keep alive through the render calls

    # --- App icons (ICO + ICNS) --------------------------------------------
    print(f"rendering {len(WIN_SIZES)} ICO sizes from {ICON_SOURCE_SVG.name}")
    write_ico(ICON_SOURCE_SVG, ICO_PATH, WIN_SIZES)
    print(f"  wrote {ICO_PATH.relative_to(ROOT)} ({ICO_PATH.stat().st_size:,} bytes)")

    print(f"rendering ICNS with {len(ICNS_CHUNKS)} chunks")
    write_icns(ICON_SOURCE_SVG, ICNS_PATH, ICNS_CHUNKS)
    print(f"  wrote {ICNS_PATH.relative_to(ROOT)} ({ICNS_PATH.stat().st_size:,} bytes)")

    # --- Installer wizard art (BMP) ---------------------------------------
    INSTALLER_ASSETS.mkdir(parents=True, exist_ok=True)
    print(f"rendering wizard BMPs from {WIZARD_SOURCE_SVG.name}")
    for label, scale in WIZARD_DPI_SCALES:
        banner_wh = (int(BANNER_BASE[0] * scale), int(BANNER_BASE[1] * scale))
        header_wh = (int(HEADER_BASE[0] * scale), int(HEADER_BASE[1] * scale))
        banner_path = INSTALLER_ASSETS / f"wizard-banner-{label}.bmp"
        header_path = INSTALLER_ASSETS / f"wizard-header-{label}.bmp"
        render_wizard_bmp(WIZARD_SOURCE_SVG, banner_path, banner_wh)
        render_wizard_bmp(WIZARD_SOURCE_SVG, header_path, header_wh)
        print(
            f"  wrote {banner_path.relative_to(ROOT)} {banner_wh} and "
            f"{header_path.relative_to(ROOT)} {header_wh}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
