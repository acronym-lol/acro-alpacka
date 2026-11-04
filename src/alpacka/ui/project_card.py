"""Single project card widget used in the library grid.

All cards are a uniform fixed height so the grid reads as a regular
tile layout — no size variation from thumbnail aspect ratio, description
length, or number of .toe files.

Layout (top → bottom):
    Project Name                                    [ ⋮ ]
    ───────────────────────────────────────────────────── (visual boundary)
    [   fixed-height thumbnail slot, letterboxed    ]
    Last opened <relative time>   <kind or TD version>
    Description (up to 2 lines, elided with "…")
    main.toe / $ npm run dev / etc.  (kind-specific target summary)

    [ 📁 ] [ </> ] [ >_ ] [ GH? ] [ 🌐? ]
    [ ─────── Launch (N) ─────── ]

For TD projects the target row shows the .toe selection (editable via
`default_toes`). For other kinds it shows a short summary of the default
run config; the card's Launch button invokes that config.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPaintEvent,
    QPainterPath,
    QPixmap,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import approval, external, kinds, process_tracker
from ..library import LibraryEntry
from . import dnd, placeholder


# Icon files live in the package resources directory.
_ICONS_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"

# Every card is exactly this tall so the grid reads as a uniform tile
# layout. Thumbnail is letterboxed into a fixed slot; description is
# elided to a fixed 2-line box. Height chosen to fit title + meta +
# thumbnail + description + toe summary + tag chips + action rows with
# comfortable spacing (no overlap). Compact height drops the thumbnail
# and description for dense browsing of large libraries.
CARD_HEIGHT = 420
CARD_HEIGHT_COMPACT = 232
THUMB_HEIGHT = 150

_THUMB_CORNER_RADIUS = 4

# Max chips rendered inline on a card before they roll up into "+K".
_MAX_VISIBLE_TAG_CHIPS = 3
# A_BLACK — letterbox fill behind thumbnails whose aspect ratio doesn't
# match the fixed slot.
_THUMB_BG = QColor("#0C0C0C")
# Placeholder text color (MUD PUDDLE darkened) — shown when a project
# has no `.launcher-thumb.png`.
_THUMB_PLACEHOLDER_COLOR = QColor("#555959")
# Drop shadow params for the thumbnail tile itself — softer and more
# spread out than the icon-badge shadow inside the placeholder, since
# this lifts the whole tile off the card surface rather than calling
# out a small element on a gradient.
_THUMB_SHADOW_BLUR = 12
_THUMB_SHADOW_OFFSET = (0, 3)
_THUMB_SHADOW_COLOR = QColor(0, 0, 0, 110)


def _layout_to_max_lines(text: str, font: QFont, width: int, max_lines: int) -> str:
    """Wrap `text` to at most `max_lines` at `width`; elide overflow with "…"."""
    if not text or width <= 0 or max_lines <= 0:
        return ""

    layout = QTextLayout(text, font)
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(opt)
    layout.beginLayout()
    lines: list[tuple[int, int]] = []
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(float(width))
        lines.append((line.textStart(), line.textLength()))
        if len(lines) >= max_lines:
            break
    layout.endLayout()

    if not lines:
        return text

    last_start, last_len = lines[-1]
    end = last_start + last_len
    if end >= len(text):
        return text  # full text fits within max_lines

    fm = QFontMetrics(font)
    kept_head = text[:last_start]
    tail = text[last_start:]
    elided_tail = fm.elidedText(tail, Qt.TextElideMode.ElideRight, width)
    return kept_head + elided_tail


class _ElidedLabel(QLabel):
    """QLabel that elides text into at most ``max_lines`` lines."""

    def __init__(
        self,
        text: str = "",
        max_lines: int = 2,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._full_text = text or ""
        self._max_lines = max_lines
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

    def setFullText(self, text: str) -> None:
        self._full_text = text or ""
        self._update_display()

    def fullText(self) -> str:
        return self._full_text

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_display()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._update_display()

    def _update_display(self) -> None:
        w = self.width()
        if w <= 0:
            # Layout hasn't resolved yet; show the raw text so Qt has
            # something to measure during the first layout pass.
            super().setText(self._full_text)
            return
        super().setText(
            _layout_to_max_lines(self._full_text, self.font(), w, self._max_lines)
        )


class _ProjectThumbnail(QWidget):
    """Fixed-height thumbnail slot with letterbox fill.

    Every card renders one of these, so cards stay the same height even
    when a project has no image. Images at ``image_path`` scale with
    ``KeepAspectRatio`` and letterbox against A_BLACK when their aspect
    ratio doesn't match the slot.

    When ``image_path`` is missing and ``placeholder_name`` is supplied,
    the slot shows a generated placeholder (hash-seeded gradient,
    initials, kind-icon badge). Generation is delegated to
    ``ui.placeholder``; results memoize by ``(name, kind_icon, w, h)``.
    Without placeholder context, falls back to the legacy "No thumbnail"
    text.
    """

    def __init__(
        self,
        image_path: Path | None,
        *,
        placeholder_name: str = "",
        placeholder_kind_icon: str = "",
        height: int = THUMB_HEIGHT,
        radius: int = _THUMB_CORNER_RADIUS,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._source = QPixmap(str(image_path)) if image_path else QPixmap()
        self._placeholder_name = placeholder_name
        self._placeholder_kind_icon = placeholder_kind_icon
        self._radius = radius
        self.setFixedHeight(height)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        # Soft drop shadow under the rounded tile so it reads as lifted
        # off the card surface. Same pattern the card uses for its own
        # outer shadow — Qt handles the blur and bounds extension; we
        # just hand it the tunables.
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(_THUMB_SHADOW_BLUR)
        shadow.setOffset(*_THUMB_SHADOW_OFFSET)
        shadow.setColor(_THUMB_SHADOW_COLOR)
        self.setGraphicsEffect(shadow)

    def has_image(self) -> bool:
        return not self._source.isNull()

    def paintEvent(self, event: QPaintEvent) -> None:
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        if self._radius > 0:
            path = QPainterPath()
            path.addRoundedRect(0, 0, w, h, self._radius, self._radius)
            painter.setClipPath(path)

        # Letterbox fill covers any aspect mismatch and doubles as the
        # background while the placeholder gradient is computed.
        painter.fillRect(0, 0, w, h, _THUMB_BG)

        if not self._source.isNull():
            scaled = self._source.scaled(
                w,
                h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (w - scaled.width()) // 2
            y = (h - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        elif self._placeholder_name:
            # Render at slot size so the gradient extends edge-to-edge
            # without letterbox bars. The module-level cache keeps repeat
            # paints cheap at the same size.
            pm = placeholder.make_placeholder(
                self._placeholder_name,
                self._placeholder_kind_icon,
                w,
                h,
            )
            painter.drawPixmap(0, 0, pm)
        else:
            painter.setPen(_THUMB_PLACEHOLDER_COLOR)
            font = painter.font()
            font.setPixelSize(11)
            painter.setFont(font)
            painter.drawText(
                QRectF(0, 0, w, h),
                Qt.AlignmentFlag.AlignCenter,
                "No thumbnail",
            )

        painter.end()


class ProjectCard(dnd.DragSource, QFrame):
    launch_requested = Signal(int, list)  # row_id, list[str] of toe filenames
    launch_new_requested = Signal(int, list)  # forces a fresh spawn even when running
    focus_requested = Signal(int, int)  # row_id, pid to focus
    stop_requested = Signal(int, int)  # row_id, pid to terminate
    open_folder_requested = Signal(int)
    open_vscode_requested = Signal(int)
    open_terminal_requested = Signal(int)
    open_repo_requested = Signal(int, str)  # row_id, url
    open_url_requested = Signal(int, str)  # row_id, url (kind-provided display_url)
    view_output_requested = Signal(int)  # row_id (inline-mode projects only)
    edit_details_requested = Signal(int)
    autostart_settings_requested = Signal(int)
    remove_requested = Signal(int)
    toggle_pin_requested = Signal(int)
    tags_changed = Signal(int, list)  # row_id, new tag list
    default_toes_changed = Signal(int, list)  # row_id, new default_toes list

    # Drag preview anchor — tip of cursor near the top of the card
    # pixmap so the card reads as "picked up from its header".
    def _drag_preview_hotspot(self, pixmap: QPixmap) -> QPoint:
        return QPoint(pixmap.width() // 2, 24)

    def _drag_mime(self):
        return dnd.project_mime(self.entry.row_id)

    def _restore_effect(self) -> None:
        # Rebuild the drop shadow — setGraphicsEffect destroyed the one
        # we had before the drag.
        self._apply_shadow()

    def __init__(
        self,
        entry: LibraryEntry,
        *,
        compact: bool = False,
        tag_colors: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.entry = entry
        self.compact = compact
        # Shared dict owned by LibraryView — kept as a reference (not a
        # copy) so chip colors reflect edits made after construction
        # without needing an explicit setter on the card.
        self._tag_colors: dict[str, str] = (
            tag_colors if tag_colors is not None else {}
        )
        self.setObjectName("projectCard")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(280)
        self.setMaximumWidth(420)
        self._launch_btn: QPushButton | None = None
        self._body: QWidget | None = None
        self._init_drag_source()

        # The outer layout holds a single ``_body`` child widget. On
        # partial updates we swap ``_body`` in place instead of calling
        # ``refresh()`` on the whole library — keeps scroll position
        # stable and avoids flicker on per-card edits.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._rebuild_body()

    def update_entry(self, entry: LibraryEntry) -> None:
        """Swap this card's data + re-render in place."""
        self.entry = entry
        self._rebuild_body()

    def _rebuild_body(self) -> None:
        # Keep `self` (which owns signals, effects, drop targets etc.)
        # but replace its contents so updated data + dynamic properties
        # re-apply cleanly. setGraphicsEffect is destroyed and rebuilt
        # every cycle — it's the only way to force QSS[pinned] / the
        # drop shadow to pick up new state.
        entry = self.entry
        self.setProperty("pinned", bool(entry.pinned))
        self.setProperty("running", self._is_running())
        self.style().unpolish(self)
        self.style().polish(self)
        self.setFixedHeight(
            CARD_HEIGHT_COMPACT if self.compact else CARD_HEIGHT
        )

        outer = self.layout()
        if self._body is not None:
            outer.removeWidget(self._body)
            self._body.deleteLater()

        self._body = QWidget()
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(18, 12, 18, 18)
        body_layout.setSpacing(6)
        self._populate(body_layout)
        outer.addWidget(self._body)
        self._apply_shadow()

    def _make_shadow(self) -> QGraphicsDropShadowEffect:
        """Build a fresh drop-shadow effect sized for this card.

        Returned as a new instance each time because ``setGraphicsEffect``
        takes ownership and destroys the previous effect — so anything
        that swaps effects (the drag-opacity dance) needs to rebuild.
        """
        shadow = QGraphicsDropShadowEffect(self)
        if self.entry.pinned:
            shadow.setBlurRadius(22)
            shadow.setOffset(0, 2)
            # MARIGOLD tint, low alpha — warms the shadow without taking over.
            shadow.setColor(QColor(253, 207, 0, 80))
        else:
            shadow.setBlurRadius(18)
            shadow.setOffset(0, 4)
            shadow.setColor(QColor(0, 0, 0, 110))
        return shadow

    def _apply_shadow(self) -> None:
        self.setGraphicsEffect(self._make_shadow())

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _populate(self, layout: QVBoxLayout) -> None:
        """Add every internal widget to the card body's layout.

        Called on first construction and on every ``update_entry`` — the
        body is recreated from scratch so dynamic properties + data
        re-render without stale state.
        """
        # Title row — carries its own border-bottom via QSS for the
        # visual boundary beneath the project name.
        layout.addWidget(self._build_title_row())

        # Meta row (last-opened + TD version) sits in the header area.
        layout.addLayout(self._build_meta_row())
        layout.addSpacing(2)

        if not self.compact:
            # Fixed-height thumbnail slot. Keeps the grid uniform
            # regardless of whether a project has `.launcher-thumb.png`.
            # When there's no real thumbnail, the slot generates one from
            # the project name + kind icon so the grid never feels empty.
            proj = self.entry.project
            has_thumb = bool(proj and proj.has_thumbnail)
            thumb_path = proj.thumbnail_path if has_thumb and proj else None
            placeholder_name = "" if has_thumb else (proj.name if proj else "")
            placeholder_icon = ""
            if not has_thumb and proj:
                handler = kinds.get(proj.kind)
                placeholder_icon = getattr(handler, "icon", "") or ""
            layout.addWidget(
                _ProjectThumbnail(
                    thumb_path,
                    placeholder_name=placeholder_name,
                    placeholder_kind_icon=placeholder_icon,
                )
            )
            layout.addSpacing(2)

            # Description is always rendered (even when empty) so the
            # layout is deterministic across cards.
            desc_text = (
                self.entry.project.description
                if self.entry.project and self.entry.project.description
                else ""
            )
            desc = _ElidedLabel(desc_text, max_lines=2)
            desc.setObjectName("cardDescription")
            desc.setFixedHeight(32)
            layout.addWidget(desc)

        layout.addWidget(self._build_target_row())
        layout.addWidget(self._build_tags_row())

        layout.addStretch(1)
        layout.addSpacing(2)
        layout.addLayout(self._build_icon_row())
        layout.addSpacing(2)
        layout.addLayout(self._build_run_row())

    def _build_title_row(self) -> QWidget:
        row = QWidget()
        row.setObjectName("cardTitleRow")
        layout = QHBoxLayout(row)
        # Bottom padding gives the border-bottom a little breathing room.
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(6)

        if self.entry.pinned:
            pin_icon = QLabel()
            pin_icon.setObjectName("cardPinIcon")
            pin_icon.setPixmap(
                QIcon(str(_ICONS_DIR / "pin.svg")).pixmap(QSize(18, 18))
            )
            pin_icon.setFixedSize(18, 20)
            pin_icon.setToolTip("Pinned")
            pin_icon.setAlignment(
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
            )
            layout.addWidget(pin_icon)

        name_label = QLabel(self._display_name())
        name_label.setObjectName("cardName")
        layout.addWidget(name_label, stretch=1)

        if self._is_stale():
            warn = QLabel("⚠ stale")
            warn.setObjectName("cardWarning")
            layout.addWidget(warn)

        # Autostart / monitoring settings. Always visible; the
        # [active="true"] dynamic property swaps in a green-tinted look
        # when any autostart feature is enabled (launch-on-startup or
        # reopen-on-crash) so the card still advertises its state at a
        # glance without hiding the button when nothing is configured.
        self._autostart_btn = QPushButton()
        self._autostart_btn.setObjectName("cardAutoStartButton")
        self._autostart_btn.setFlat(True)
        self._autostart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # Active reflects effective runtime behavior: Auto Relaunch
        # requires Auto Launch, so `launch_on_startup=False` means
        # nothing auto-starts regardless of reopen_on_crash. Mirror the
        # runtime invariant here so the card doesn't advertise a
        # dead config.
        autostart_active = bool(
            self.entry.project and self.entry.project.launch_on_startup
        )
        # Zap icon swaps between MATRIX GREEN (active) and a warm gray
        # (inactive) so the off state is unmistakable even before the
        # button's background tint reads. Qt's QIcon doesn't honor QSS
        # color on SVGs, so we ship two files and swap at render time.
        icon_name = "zap.svg" if autostart_active else "zap-muted.svg"
        self._autostart_btn.setIcon(QIcon(str(_ICONS_DIR / icon_name)))
        self._autostart_btn.setIconSize(QSize(16, 16))
        self._autostart_btn.setFixedSize(28, 26)
        self._autostart_btn.setToolTip("Auto Launch & Relaunch settings")
        self._autostart_btn.setProperty(
            "active", "true" if autostart_active else "false"
        )
        # Absorb the QAbstractButton.clicked(bool) payload so the signal
        # carries the row_id and not the checked state — same pattern as
        # `_make_icon_button`.
        self._autostart_btn.clicked.connect(
            lambda _checked=False, rid=self.entry.row_id:
                self.autostart_settings_requested.emit(rid)
        )
        layout.addWidget(
            self._autostart_btn,
            alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )

        self._tag_btn = self._make_icon_button(
            "tag.svg", "Edit tags", self._emit_edit_tags
        )
        # The title-row icon buttons are sized tighter than the action-row
        # buttons — shrink the tag button to visually balance the ⋯ glyph
        # next to it without taking more space than it needs.
        self._tag_btn.setFixedSize(28, 26)
        self._tag_btn.setIconSize(QSize(16, 16))
        layout.addWidget(
            self._tag_btn,
            alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )

        self._more_btn = QPushButton()
        self._more_btn.setObjectName("cardMenuButton")
        self._more_btn.setIcon(QIcon(str(_ICONS_DIR / "ellipsis.svg")))
        self._more_btn.setIconSize(QSize(22, 22))
        self._more_btn.setFixedSize(30, 26)
        self._more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more_btn.setFlat(True)
        self._more_btn.clicked.connect(self._show_more_menu)
        layout.addWidget(
            self._more_btn, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight
        )

        return row

    def _build_meta_row(self) -> QHBoxLayout:
        """Last-opened on the left, TD version on the right."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        time_label = QLabel(self._last_opened_text())
        time_label.setObjectName("cardMeta")
        time_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        row.addWidget(time_label)

        row.addStretch(1)

        version_label = QLabel(self._version_text())
        version_label.setObjectName("cardVersion")
        version_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        row.addWidget(version_label)

        return row

    def _build_target_row(self) -> QWidget:
        """Kind-specific "what will launch" summary.

        - TouchDesigner: toe filename or a picker button for multi-toe.
        - Other kinds: a short summary of the default run config
          (``$ npm run dev``, ``$ python hello.py``, etc.). Clicking
          doesn't do anything yet — users pick a run config via Edit
          Details.
        """
        if self.entry.project is not None and self.entry.project.kind != "touchdesigner":
            summary = self._non_td_target_summary()
            label = QLabel(summary)
            label.setObjectName("cardToe")
            label.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            label.setToolTip(summary)
            return label

        toes = self._all_toe_filenames()

        if not toes:
            label = QLabel("(no .toe)")
            label.setObjectName("cardToe")
            label.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            return label

        if len(toes) == 1:
            label = QLabel(toes[0])
            label.setObjectName("cardToe")
            label.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            return label

        btn = QPushButton()
        btn.setObjectName("cardToeButton")
        btn.setFlat(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        btn.clicked.connect(lambda _=False: self._show_toe_menu())
        self._toe_button = btn
        self._refresh_toe_button_text()
        return btn

    def _non_td_target_summary(self) -> str:
        """A one-line summary of what the Launch button will do for a
        non-TD project. Delegates to the kind handler's `target_summary`
        so each kind picks the most informative label it can show
        (resolved package-manager script for nodejs, entry path for
        python, executable path for executable, etc.)."""
        proj = self.entry.project
        if proj is None:
            return ""
        # User's alpacka.yml can override with a concrete default command.
        for raw in proj.run_configs:
            if raw.get("name") in (None, "", "default") and raw.get("command"):
                return f"$ {raw['command']}"
        handler = kinds.get(proj.kind)
        try:
            return handler.target_summary(proj)
        except Exception:  # noqa: BLE001
            return f"Run via {handler.display_name}"

    def _refresh_toe_button_text(self) -> None:
        btn = getattr(self, "_toe_button", None)
        if btn is None:
            return
        selected = self._effective_toes()
        if not selected:
            label = "(none)"
        elif len(selected) == 1:
            label = selected[0]
        else:
            # "example.toe +1" — primary name with a small count of the
            # extras. Full list lives in the tooltip for cases where
            # users want to confirm exactly what's selected.
            label = f"{selected[0]} +{len(selected) - 1}"
        # Trailing chevron keeps the dropdown affordance on the right
        # where the user's eye expects it.
        btn.setText(f"{label}  ⌄")
        full = ", ".join(selected) if selected else "(none)"
        btn.setToolTip(
            "Click to choose which .toe files launch.\n\n" + full
        )

    def _show_toe_menu(self) -> None:
        """Open the modal .toe picker and persist the result on save."""
        if self.entry.project is None:
            return
        all_toes = self._all_toe_filenames()
        if not all_toes:
            return
        # Local import — mirrors the tag picker pattern and avoids an
        # eager dialog import during the card's construction.
        from .toe_picker_dialog import ToePickerDialog

        picked = ToePickerDialog.pick(
            self,
            self._display_name(),
            all_toes,
            self._effective_toes(),
        )
        if picked is None:
            return

        # Stable order — always list toes in the project's canonical
        # order (primary first, then alternates) regardless of check
        # sequence in the dialog.
        ordered = [t for t in all_toes if t in picked]
        if not ordered and self.entry.project.toe_file:
            ordered = [self.entry.project.toe_file]
        self.default_toes_changed.emit(self.entry.row_id, list(ordered))

    def _build_tags_row(self) -> QWidget:
        """Compact chip row showing up to ``_MAX_VISIBLE_TAG_CHIPS`` tags.

        Extras roll into a single ``+K`` chip whose tooltip lists the
        full tag set so nothing is hidden from the user.
        """
        wrapper = QWidget()
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        tag_names = list(self.entry.project.tags) if self.entry.project else []
        if not tag_names:
            row.addStretch(1)
            return wrapper

        max_visible = _MAX_VISIBLE_TAG_CHIPS
        visible = tag_names[:max_visible]
        overflow = len(tag_names) - len(visible)
        for name in visible:
            row.addWidget(
                self._make_tag_chip(
                    name, color=self._tag_colors.get(name, "")
                )
            )
        if overflow > 0:
            # The "+K" chip stays untinted — it's a count, not a tag.
            more = self._make_tag_chip(f"+{overflow}")
            more.setToolTip(", ".join(tag_names))
            row.addWidget(more)
        row.addStretch(1)
        return wrapper

    def _make_tag_chip(self, text: str, color: str = "") -> QLabel:
        chip = QLabel(text)
        chip.setObjectName("cardTagChip")
        chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if color:
            c = QColor(color)
            if c.isValid():
                # Inline styles layer on top of the #cardTagChip block
                # in style.qss — shared padding / radius / font-size
                # come from there; only the palette swaps per tag.
                bg = f"rgba({c.red()}, {c.green()}, {c.blue()}, 40)"
                chip.setStyleSheet(
                    f"background-color: {bg}; "
                    f"border: 1px solid {c.name()}; "
                    f"color: {c.lighter(130).name()};"
                )
        return chip

    def _emit_edit_tags(self) -> None:
        """Open the tag picker modal and persist whatever the user picks."""
        # Local import — the card module otherwise has no UI dialog
        # imports, and we don't want a cycle if dialogs gain card
        # references later.
        from .tag_picker_dialog import TagPickerDialog
        current = (
            list(self.entry.project.tags)
            if self.entry.project is not None
            else []
        )
        picked = TagPickerDialog.pick(self, current)
        if picked is None:
            return
        self.tags_changed.emit(self.entry.row_id, list(picked))


    def _build_icon_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)

        row.addWidget(
            self._make_icon_button(
                "folder.svg",
                "Open folder",
                lambda: self.open_folder_requested.emit(self.entry.row_id),
            )
        )
        row.addWidget(
            self._make_icon_button(
                "vscode.svg",
                "Open in VS Code",
                lambda: self.open_vscode_requested.emit(self.entry.row_id),
            )
        )
        row.addWidget(
            self._make_icon_button(
                "terminal.svg",
                "Open terminal here",
                lambda: self.open_terminal_requested.emit(self.entry.row_id),
            )
        )

        # Inline-mode projects get a "view output" button so users can
        # tail captured stdout/stderr inside ALPACKA. Always visible
        # for inline-mode projects (even when the buffer is empty)
        # since the dialog has its own empty-state copy — discovery
        # beats tidiness here per the design review.
        if self._show_view_output_button():
            row.addWidget(
                self._make_icon_button(
                    "logs.svg",
                    "View captured output",
                    lambda: self.view_output_requested.emit(self.entry.row_id),
                )
            )

        repo_url = external.detect_git_remote_url(self.entry.path)
        if repo_url:
            row.addWidget(
                self._make_icon_button(
                    "github.svg",
                    f"Open repo in browser\n{repo_url}",
                    lambda url=repo_url: self.open_repo_requested.emit(
                        self.entry.row_id, url
                    ),
                )
            )

        display_url = self._kind_display_url()
        if display_url:
            row.addWidget(
                self._make_icon_button(
                    "globe.svg",
                    f"Open in browser\n{display_url}",
                    lambda url=display_url: self.open_url_requested.emit(
                        self.entry.row_id, url
                    ),
                )
            )

        row.addStretch(1)
        return row

    def _kind_display_url(self) -> str | None:
        """Resolve the kind's `display_url(project, variables)` for this card.

        Returns None if the project's kind handler doesn't publish a URL
        (or the project is missing / unreadable).
        """
        proj = self.entry.project
        if proj is None:
            return None
        try:
            handler = kinds.get(proj.kind)
            url = handler.display_url(proj, dict(proj.variables))
        except Exception:  # noqa: BLE001
            return None
        if not url:
            return None
        if not (str(url).startswith("http://") or str(url).startswith("https://")):
            return None
        return str(url)

    def _show_view_output_button(self) -> bool:
        """True for non-TD projects whose effective output_mode is inline."""
        proj = self.entry.project
        if proj is None or proj.kind == kinds.default_kind():
            return False
        try:
            handler = kinds.get(proj.kind)
            return proj.effective_output_mode(handler) == "inline"
        except Exception:  # noqa: BLE001
            return False

    def _make_icon_button(self, icon_file: str, tooltip: str, handler) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("cardIconButton")
        btn.setFlat(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tooltip)
        btn.setFixedSize(34, 30)
        btn.setIcon(QIcon(str(_ICONS_DIR / icon_file)))
        btn.setIconSize(QSize(18, 18))
        # Wrap to absorb the QAbstractButton.clicked(bool) payload so that
        # caller-supplied zero-arg lambdas (and lambdas with kwargs that
        # could otherwise collide with a positional bool) stay intact.
        btn.clicked.connect(lambda _checked=False, h=handler: h())
        return btn

    def _build_run_row(self) -> QHBoxLayout:
        """The bottom row of the card: Launch button alone when idle, or
        [Focus Running] + [Stop] when a process is tracked for this project.
        Labels are ALL CAPS + bold to read as clear, assertive actions;
        the idle Launch button gets a Lucide play glyph.
        """
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        launch_btn = QPushButton(self._launch_label())
        launch_btn.setObjectName("launchButton")
        launch_btn.setProperty("running", self._is_running())
        # Run settings not reviewed on this machine yet: Launch shows them
        # first (approval.py). Amber outline + label so it's visible.
        needs_review = not self._is_running() and not approval.is_approved(self.entry)
        launch_btn.setProperty("needsReview", needs_review)
        if needs_review:
            launch_btn.setToolTip(
                "ALPACKA hasn't run this project's settings on this machine yet. "
                "Launch shows what it runs and asks first."
            )
        launch_btn.setFixedHeight(34)
        launch_btn.clicked.connect(self._emit_launch)
        launch_btn.setEnabled(not self._should_block_launch())
        if not self._is_running():
            # Only the idle state carries the play glyph; the running
            # state uses its own "●" status dot in the label text.
            launch_btn.setIcon(QIcon(str(_ICONS_DIR / "play.svg")))
            launch_btn.setIconSize(QSize(12, 12))
        self._launch_btn = launch_btn
        row.addWidget(launch_btn, stretch=1)

        if self._is_running():
            stop_btn = QPushButton()
            stop_btn.setObjectName("cardStopButton")
            stop_btn.setIcon(QIcon(str(_ICONS_DIR / "stop.svg")))
            stop_btn.setIconSize(QSize(14, 14))
            stop_btn.setFixedHeight(34)
            stop_btn.setFixedWidth(42)
            stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            stop_btn.setToolTip("Stop the running process")
            stop_btn.clicked.connect(self._emit_stop)
            row.addWidget(stop_btn)

        return row

    def _emit_stop(self) -> None:
        """Stop the first tracked process. When multiple are running,
        repeated Stop clicks walk through them (the card re-renders after
        each termination, so the "first" becomes the next survivor)."""
        if not self._is_running():
            return
        first = self._active_processes()[0]
        self.stop_requested.emit(self.entry.row_id, first.pid)

    def _launch_label(self) -> str:
        if self._is_running():
            count = len(self._active_processes())
            suffix = f" ({count})" if count > 1 else ""
            return f"● RUNNING{suffix}"
        # TD can launch multiple toes at once; non-TD kinds fire a single
        # run config per click.
        proj = self.entry.project
        prefix = "REVIEW && LAUNCH" if not approval.is_approved(self.entry) else "LAUNCH"
        if proj is not None and proj.kind == "touchdesigner":
            selected = self._effective_toes()
            if len(selected) > 1:
                return f"{prefix} ({len(selected)})"
        return prefix

    def _active_processes(self) -> list:
        # Finished one-shot runs stay in the table for their exit code but
        # aren't running — they must not show Stop or Focus.
        return process_tracker.active(self.entry.running_processes)

    def _is_running(self) -> bool:
        return bool(self._active_processes())

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _display_name(self) -> str:
        if self.entry.project and self.entry.project.name:
            return self.entry.project.name
        return self.entry.path.name

    def _all_toe_filenames(self) -> list[str]:
        if self.entry.project is None:
            return []
        return self.entry.project.all_toe_filenames()

    def _effective_toes(self) -> list[str]:
        """The .toe files that will launch when Launch is pressed."""
        if self.entry.project is None:
            return []
        return self.entry.project.effective_launch_toes()

    def _version_text(self) -> str:
        """Kind-specific badge text shown in the meta row.

        TouchDesigner gets its pinned version ("TD 2025.32280"); other
        kinds get their display name ("Node.js", "Python", etc.) so the
        user can tell at a glance what kind of project a card is.
        """
        proj = self.entry.project
        if proj is None:
            return ""
        if proj.kind == "touchdesigner":
            if not proj.td_version:
                return "TD version not set"
            return f"TD {proj.td_version}"
        return kinds.get(proj.kind).display_name

    def _last_opened_text(self) -> str:
        ts = self.entry.last_launched or (
            self.entry.project.last_opened if self.entry.project else ""
        )
        if not ts:
            return "Never opened"
        relative = _format_relative(ts)
        return f"Last opened {relative}" if relative else "Never opened"

    def _is_stale(self) -> bool:
        if self.entry.stale:
            return True
        return bool(self.entry.project and self.entry.project.is_stale)

    def _should_block_launch(self) -> bool:
        if self.entry.project is None:
            return True
        return self.entry.project.is_stale

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _emit_launch(self) -> None:
        # When anything is running, the Launch button's job is to focus the
        # first existing instance instead of spawning a new one. "Launch
        # new instance" in the ⋯ menu handles the "I really want another
        # copy" case.
        if self._is_running():
            first = self._active_processes()[0]
            self.focus_requested.emit(self.entry.row_id, first.pid)
            return
        targets = self._launch_targets()
        if not targets:
            return
        self.launch_requested.emit(self.entry.row_id, targets)

    def _show_more_menu(self) -> None:
        menu = QMenu(self)

        # Running-state launch-new action (Stop lives inline next to
        # Focus Running on the card — not here — so users don't have to
        # dive into a submenu just to close a scene).
        if self._is_running():
            menu.addAction("Launch new instance", self._emit_launch_new)
            menu.addSeparator()

        menu.addAction(
            "Edit Details",
            lambda: self.edit_details_requested.emit(self.entry.row_id),
        )
        menu.addSeparator()
        pin_label = "Unpin" if self.entry.pinned else "Pin"
        menu.addAction(
            pin_label,
            lambda: self.toggle_pin_requested.emit(self.entry.row_id),
        )
        menu.addSeparator()
        menu.addAction(
            "Remove from Library",
            lambda: self.remove_requested.emit(self.entry.row_id),
        )
        # Drop beneath the button, right-aligned to its edge.
        btn = self._more_btn
        anchor = btn.mapToGlobal(btn.rect().bottomRight())
        menu_size = menu.sizeHint()
        anchor.setX(anchor.x() - menu_size.width())
        menu.exec(anchor)

    def _emit_launch_new(self) -> None:
        targets = self._launch_targets()
        if not targets and self.entry.project and self.entry.project.toe_file:
            targets = [self.entry.project.toe_file]
        if not targets:
            return
        self.launch_new_requested.emit(self.entry.row_id, targets)

    def _launch_targets(self) -> list[str]:
        """Items for launch_requested — toe filenames for TD, run-config
        names (typically just ``["default"]``) for other kinds."""
        proj = self.entry.project
        if proj is None:
            return []
        if proj.kind == "touchdesigner":
            return list(self._effective_toes())
        # Non-TD: one run_config per click. If alpacka.yml declares a
        # "default" entry, use its name (already "default"). Otherwise
        # the literal "default" triggers the kind handler's fallback.
        return ["default"]


def _format_relative(iso_timestamp: str) -> str:
    try:
        ts = iso_timestamp.rstrip("Z")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        delta = datetime.utcnow() - dt
    except (ValueError, TypeError):
        return ""

    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    if seconds < 86400 * 7:
        return f"{seconds // 86400}d ago"
    return dt.strftime("%Y-%m-%d")
