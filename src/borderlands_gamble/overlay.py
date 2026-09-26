"""
The on-screen slot machine, drawn with UMG widgets built at runtime - no custom assets needed.

This follows the same construction pattern other BL4 SDK mods use for their native menus: a bare
`UserWidget` with a `WidgetTree`, a `CanvasPanel` root, and `Border`/`TextBlock` children. The
overlay is hit-test invisible, so it never steals input from the game.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import unrealsdk
from mods_base import get_pc
from unrealsdk import logging
from unrealsdk.unreal import UObject, WeakPointer

from .casino import OverlayView, Tone
from .slots import SYMBOL_COLORS, SYMBOL_LABELS

if TYPE_CHECKING:
    from collections.abc import Callable

RGBA = tuple[float, float, float, float]

# ESlateVisibility
COLLAPSED = 1
HIT_TEST_INVISIBLE = 3

# ETextJustify
JUSTIFY_CENTER = 1

VIEWPORT_Z = 999_000

# Layout, in 1080p units relative to the centre of the panel.
DESIGN_W = 1920.0
DESIGN_H = 1080.0
PANEL_W = 800.0
PANEL_H = 390.0
REEL_W = 220.0
REEL_H = 210.0
REEL_SPACING = 240.0
ROW_H = 70.0
REELS_TOP = -125.0

# How far the panel's centre sits from the top/bottom of the screen, in the same units.
POSITION_MARGINS = {"Top": 265.0, "Bottom": 350.0}
POSITIONS = ("Top", "Center", "Bottom")

GOLD: RGBA = (0.95, 0.72, 0.18, 1.0)
PANEL_BG: RGBA = (0.04, 0.03, 0.07, 0.92)
REEL_BG: RGBA = (0.10, 0.10, 0.13, 1.0)
FOOTER_COLOR: RGBA = (0.70, 0.70, 0.70, 1.0)
SHADOW: RGBA = (0.0, 0.0, 0.0, 0.85)

TONE_COLORS: dict[Tone, RGBA] = {
    Tone.INFO: (0.92, 0.92, 0.92, 1.0),
    Tone.WIN: (0.45, 1.00, 0.45, 1.0),
    Tone.BIG_WIN: (1.00, 0.80, 0.20, 1.0),
    Tone.JACKPOT: (1.00, 0.55, 0.05, 1.0),
    Tone.LOSE: (0.70, 0.70, 0.70, 1.0),
    Tone.ERROR: (1.00, 0.35, 0.30, 1.0),
}
PAYLINE_COLORS: dict[Tone, RGBA] = {
    Tone.BIG_WIN: (0.95, 0.72, 0.18, 0.30),
    Tone.JACKPOT: (1.00, 0.55, 0.05, 0.50),
}
PAYLINE_DEFAULT: RGBA = (1.0, 1.0, 1.0, 0.08)

TITLE_SCALE = 1.25
SYMBOL_SCALE = 1.35
EDGE_SYMBOL_SCALE = 1.0
STATUS_SCALE = 1.05
FOOTER_SCALE = 0.75
EDGE_SYMBOL_ALPHA = 0.35
SPINNING_SYMBOL_ALPHA = 0.85


def _class(path: str) -> UObject:
    return unrealsdk.find_object("Class", path)


def _vec2(x: float, y: float) -> Any:
    return unrealsdk.make_struct("Vector2D", X=float(x), Y=float(y))


def _linear(color: RGBA) -> Any:
    r, g, b, a = color
    return unrealsdk.make_struct("LinearColor", R=float(r), G=float(g), B=float(b), A=float(a))


def _slate(color: RGBA) -> Any:
    return unrealsdk.make_struct("SlateColor", SpecifiedColor=_linear(color), ColorUseRule=0)


@dataclass
class _Widgets:
    root: UObject
    title: UObject
    reels: list[tuple[UObject, UObject, UObject]]
    payline: UObject
    status: UObject
    footer: UObject


class _Builder:
    """Places widgets on a canvas, converting from design units to screen units."""

    def __init__(self, root: UObject, canvas: UObject, center: tuple[float, float], scale: float) -> None:
        self.root = root
        self.canvas = canvas
        self.center = center
        self.scale = scale

    def _construct(self, path: str) -> UObject:
        widget = unrealsdk.construct_object(_class(path), self.root.WidgetTree)
        widget.SetVisibility(HIT_TEST_INVISIBLE)
        self.canvas.AddChild(widget)
        return widget

    def _pos(self, x: float, y: float) -> Any:
        return _vec2(self.center[0] + x * self.scale, self.center[1] + y * self.scale)

    def box(self, x: float, y: float, w: float, h: float, color: RGBA, z: int) -> UObject:
        border = self._construct("/Script/UMG.Border")
        border.SetBrushColor(_linear(color))
        slot = border.Slot
        slot.SetAutoSize(False)
        slot.SetPosition(self._pos(x, y))
        slot.SetSize(_vec2(w * self.scale, h * self.scale))
        slot.SetZOrder(z)
        return border

    def text(self, x: float, y: float, size: float, z: int) -> UObject:
        """Adds a text block centred on the given point."""
        block = self._construct("/Script/UMG.TextBlock")
        block.SetJustification(JUSTIFY_CENTER)
        block.SetRenderTransformPivot(_vec2(0.5, 0.5))
        block.SetRenderScale(_vec2(size * self.scale, size * self.scale))
        block.SetShadowOffset(_vec2(1.5, 1.5))
        block.SetShadowColorAndOpacity(_linear(SHADOW))
        slot = block.Slot
        slot.SetAutoSize(True)
        slot.SetAlignment(_vec2(0.5, 0.5))
        slot.SetPosition(self._pos(x, y))
        slot.SetZOrder(z)
        return block


class UmgOverlay:
    """Implements `casino.Display` with native UMG widgets."""

    def __init__(self, layout: Callable[[], tuple[float, str]]) -> None:
        """
        Args:
            layout: Returns the current (scale, position) the overlay should be drawn at.
        """
        self.layout = layout
        self._root: WeakPointer = WeakPointer()
        self._widgets: _Widgets | None = None
        self._built_layout: tuple[float, str] | None = None
        self._cache: dict[tuple[int, str], Any] = {}
        self._visible = False
        self._reported_failure = False

    def render(self, view: OverlayView) -> None:
        try:
            widgets = self._ensure_built()
            self._apply(widgets, view)
            if not self._visible:
                widgets.root.SetVisibility(HIT_TEST_INVISIBLE)
                self._visible = True
            self._reported_failure = False
        except Exception as ex:  # noqa: BLE001 - never let drawing break the game
            if not self._reported_failure:
                logging.error(f"[Borderlands Gamble] Couldn't draw the slot machine: {ex!r}")
                logging.error("[Borderlands Gamble] Results will still be printed to console.")
                self._reported_failure = True

    def hide(self) -> None:
        if not self._visible:
            return
        self._visible = False
        if self._widgets is None or self._root() is None:
            return
        try:
            self._widgets.root.SetVisibility(COLLAPSED)
        except Exception as ex:  # noqa: BLE001
            logging.dev_warning(f"[Borderlands Gamble] Couldn't hide the overlay: {ex!r}")

    def destroy(self) -> None:
        root = self._root()
        if root is not None:
            try:
                root.RemoveFromParent()
            except Exception as ex:  # noqa: BLE001
                logging.dev_warning(f"[Borderlands Gamble] Couldn't remove the overlay: {ex!r}")
        self._root = WeakPointer()
        self._widgets = None
        self._built_layout = None
        self._cache.clear()
        self._visible = False

    # ==============================================================================================

    def _set(self, widget: UObject, key: str, value: Any, setter: Callable[[Any], Any]) -> None:
        """Calls a setter only if the value changed, to keep per-frame work down."""
        cache_key = (id(widget), key)
        if self._cache.get(cache_key) != value:
            setter(value)
            self._cache[cache_key] = value

    def _set_color(self, widget: UObject, color: RGBA) -> None:
        self._set(widget, "color", color, lambda c: widget.SetColorAndOpacity(_slate(c)))

    def _set_text(self, widget: UObject, text: str) -> None:
        self._set(widget, "text", text, widget.SetText)

    def _ensure_built(self) -> _Widgets:
        layout = self.layout()
        if self._root() is not None and self._widgets is not None and layout == self._built_layout:
            return self._widgets

        # Either the first draw, the old widget got cleaned up (e.g. on a map change), or the
        # layout settings changed
        self.destroy()
        self._widgets = self._build(*layout)
        self._root = WeakPointer(self._widgets.root)
        self._built_layout = layout
        return self._widgets

    def _build(self, user_scale: float, position: str) -> _Widgets:
        pc = get_pc()
        if pc is None:
            raise RuntimeError("No player controller to draw for")

        layout_w, layout_h = _layout_size(pc)
        scale = min(layout_w / DESIGN_W, layout_h / DESIGN_H) * max(0.1, user_scale)
        center_x = layout_w / 2
        match position:
            case "Center":
                center_y = layout_h / 2
            case "Bottom":
                center_y = layout_h - POSITION_MARGINS["Bottom"] * scale
            case _:
                center_y = POSITION_MARGINS["Top"] * scale

        root = unrealsdk.construct_object(_class("/Script/UMG.UserWidget"), pc)
        root.WidgetTree = unrealsdk.construct_object(_class("/Script/UMG.WidgetTree"), root)
        canvas = unrealsdk.construct_object(_class("/Script/UMG.CanvasPanel"), root.WidgetTree)
        root.WidgetTree.RootWidget = canvas

        root.SetAlignmentInViewport(_vec2(0, 0))
        root.SetPositionInViewport(_vec2(0, 0), False)
        root.SetDesiredSizeInViewport(_vec2(layout_w, layout_h))
        root.AddToViewport(VIEWPORT_Z)
        root.SetVisibility(COLLAPSED)

        ui = _Builder(root, canvas, (center_x, center_y), scale)
        half_w, half_h = PANEL_W / 2, PANEL_H / 2
        ui.box(-half_w - 4, -half_h - 4, PANEL_W + 8, PANEL_H + 8, GOLD, 0)
        ui.box(-half_w, -half_h, PANEL_W, PANEL_H, PANEL_BG, 1)

        reels: list[tuple[UObject, UObject, UObject]] = []
        for idx in range(3):
            reel_x = (idx - 1) * REEL_SPACING
            ui.box(reel_x - REEL_W / 2, REELS_TOP, REEL_W, REEL_H, REEL_BG, 2)
            above, payline, below = (
                ui.text(reel_x, REELS_TOP + ROW_H * (row + 0.5), size, 10)
                for row, size in enumerate((EDGE_SYMBOL_SCALE, SYMBOL_SCALE, EDGE_SYMBOL_SCALE))
            )
            reels.append((above, payline, below))

        band_w = 2 * REEL_SPACING + REEL_W
        payline_band = ui.box(-band_w / 2, REELS_TOP + ROW_H, band_w, ROW_H, PAYLINE_DEFAULT, 3)

        return _Widgets(
            root=root,
            title=ui.text(0, -half_h + 35, TITLE_SCALE, 10),
            reels=reels,
            payline=payline_band,
            status=ui.text(0, REELS_TOP + REEL_H + 35, STATUS_SCALE, 10),
            footer=ui.text(0, half_h - 28, FOOTER_SCALE, 10),
        )

    def _apply(self, widgets: _Widgets, view: OverlayView) -> None:
        self._set_text(widgets.title, view.title)
        self._set_color(widgets.title, GOLD)

        for rows, reel in zip(widgets.reels, view.reels, strict=True):
            payline_alpha = 1.0 if reel.stopped else SPINNING_SYMBOL_ALPHA
            for block, symbol, alpha in (
                (rows[0], reel.above, EDGE_SYMBOL_ALPHA),
                (rows[1], reel.payline, payline_alpha),
                (rows[2], reel.below, EDGE_SYMBOL_ALPHA),
            ):
                r, g, b, a = SYMBOL_COLORS[symbol]
                self._set_text(block, SYMBOL_LABELS[symbol])
                self._set_color(block, (r, g, b, a * alpha))

        band = PAYLINE_COLORS.get(view.tone, PAYLINE_DEFAULT)
        self._set(widgets.payline, "color", band, lambda c: widgets.payline.SetBrushColor(_linear(c)))

        self._set_text(widgets.status, view.status)
        self._set_color(widgets.status, TONE_COLORS[view.tone])
        self._set_text(widgets.footer, view.footer)
        self._set_color(widgets.footer, FOOTER_COLOR)


def _layout_size(pc: UObject) -> tuple[float, float]:
    """Gets the viewport size in DPI-scaled units, falling back to 1080p."""
    try:
        lib = _class("/Script/UMG.WidgetLayoutLibrary").ClassDefaultObject
        size = lib.GetViewportSize(pc)
        dpi = float(lib.GetViewportScale(pc)) or 1.0
        width, height = float(size.X) / dpi, float(size.Y) / dpi
        if width >= 640 and height >= 360:
            return width, height
    except Exception as ex:  # noqa: BLE001
        logging.dev_warning(f"[Borderlands Gamble] Couldn't read viewport size: {ex!r}")
    return DESIGN_W, DESIGN_H
