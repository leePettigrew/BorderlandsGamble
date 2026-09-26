"""
The on-screen slot machine, drawn with UMG widgets built at runtime - no custom assets needed.

This follows the same construction pattern other BL4 SDK mods use for their native menus: a bare
`UserWidget` with a `WidgetTree`, a `CanvasPanel` root, and `Border`/`TextBlock` children. The
overlay and the "press E" prompt are hit-test invisible, so they never steal input from the game.
The menu (`menu.py`) builds on the same pieces.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import unrealsdk
from mods_base import get_pc
from unrealsdk import logging
from unrealsdk.unreal import UObject, WeakPointer

from .casino import OverlayView, Tone
from .slots import SYMBOL_COLORS, SYMBOL_LABELS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from .spectate import WatchedSpin

RGBA = tuple[float, float, float, float]

# ESlateVisibility
VISIBLE = 0
COLLAPSED = 1
HIT_TEST_INVISIBLE = 3

# ETextJustify
JUSTIFY = {"left": 0, "center": 1, "right": 2}
ANCHOR = {"left": 0.0, "center": 0.5, "right": 1.0}

VIEWPORT_Z = 999_000
PROMPT_Z = 998_000

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

# Other players' reels, floating above their heads. Players off screen or far away get theirs stacked
# in the top left corner instead.
SPECTATOR_Z = 997_000
SPECTATOR_W = 380.0
SPECTATOR_H = 116.0
SPECTATOR_PANELS = 3
DOCK_X = 24.0
DOCK_Y = 140.0
DOCK_GAP = 12.0
SPECTATOR_BG: RGBA = (0.04, 0.03, 0.07, 0.82)

# The prompt sits a little below the crosshair.
PROMPT_Y = 0.62
PROMPT_W = 420.0
PROMPT_H = 56.0
PROMPT_SCALE = 0.9
PROMPT_BG: RGBA = (0.04, 0.03, 0.07, 0.80)


def ui_class(path: str) -> UObject:
    return unrealsdk.find_object("Class", path)


def vec2(x: float, y: float) -> Any:
    return unrealsdk.make_struct("Vector2D", X=float(x), Y=float(y))


def linear(color: RGBA) -> Any:
    r, g, b, a = color
    return unrealsdk.make_struct("LinearColor", R=float(r), G=float(g), B=float(b), A=float(a))


def slate(color: RGBA) -> Any:
    return unrealsdk.make_struct("SlateColor", SpecifiedColor=linear(color), ColorUseRule=0)


@dataclass
class _Widgets:
    root: UObject
    title: UObject
    reels: list[tuple[UObject, UObject, UObject]]
    payline: UObject
    status: UObject
    footer: UObject


@dataclass(frozen=True)
class Screen:
    """The viewport, in the DPI-scaled units UMG lays widgets out in."""

    width: float
    height: float
    # Rendered pixels per layout unit, e.g. to compare widget positions against the mouse
    dpi: float

    def fit(self, user_scale: float = 1.0) -> float:
        """Gets the scale that fits a 1080p design to this screen."""
        return min(self.width / DESIGN_W, self.height / DESIGN_H) * max(0.1, user_scale)


@contextmanager
def removed_on_failure(root: UObject) -> Iterator[None]:
    """Takes a half built widget back off the screen if building it fails, so they can't pile up."""
    try:
        yield
    except BaseException:
        try:
            root.RemoveFromParent()
        except Exception as ex:  # noqa: BLE001
            logging.dev_warning(f"[Borderlands Gamble] Couldn't remove a half built widget: {ex!r}")
        raise


def new_root(pc: UObject, z: int) -> tuple[UObject, UObject, Screen]:
    """Creates an empty, collapsed, full screen `UserWidget`. Returns it, its canvas, and the screen."""
    screen = screen_size(pc)
    root = unrealsdk.construct_object(ui_class("/Script/UMG.UserWidget"), pc)
    root.WidgetTree = unrealsdk.construct_object(ui_class("/Script/UMG.WidgetTree"), root)
    canvas = unrealsdk.construct_object(ui_class("/Script/UMG.CanvasPanel"), root.WidgetTree)
    root.WidgetTree.RootWidget = canvas

    root.SetAlignmentInViewport(vec2(0, 0))
    root.SetPositionInViewport(vec2(0, 0), False)
    root.SetDesiredSizeInViewport(vec2(screen.width, screen.height))
    root.AddToViewport(z)
    root.SetVisibility(COLLAPSED)
    return root, canvas, screen


class Builder:
    """Places widgets on a canvas, converting from design units to screen units."""

    def __init__(self, root: UObject, canvas: UObject, center: tuple[float, float], scale: float) -> None:
        self.root = root
        self.canvas = canvas
        self.center = center
        self.scale = scale

    def construct(
        self,
        path: str,
        visibility: int = HIT_TEST_INVISIBLE,
        setup: Callable[[UObject], None] | None = None,
    ) -> UObject:
        """
        Creates a widget on the canvas.

        Args:
            path: The widget's class.
            visibility: Its `ESlateVisibility`.
            setup: Called with the widget before it's added, for settings that only apply then.
        Returns:
            The new widget.
        """
        widget = unrealsdk.construct_object(ui_class(path), self.root.WidgetTree)
        widget.SetVisibility(visibility)
        if setup is not None:
            setup(widget)
        self.canvas.AddChild(widget)
        return widget

    def pos(self, x: float, y: float) -> tuple[float, float]:
        """Converts a design position (relative to the centre) to a canvas position."""
        return self.center[0] + x * self.scale, self.center[1] + y * self.scale

    def place(self, widget: UObject, x: float, y: float, w: float, h: float, z: int) -> None:
        slot = widget.Slot
        slot.SetAutoSize(False)
        slot.SetPosition(vec2(*self.pos(x, y)))
        slot.SetSize(vec2(w * self.scale, h * self.scale))
        slot.SetZOrder(z)

    def box(self, x: float, y: float, w: float, h: float, color: RGBA, z: int) -> UObject:
        border = self.construct("/Script/UMG.Border")
        border.SetBrushColor(linear(color))
        self.place(border, x, y, w, h, z)
        return border

    def text(self, x: float, y: float, size: float, z: int, align: str = "center") -> UObject:
        """Adds a text block anchored on the given point: its left edge, centre, or right edge."""
        anchor = ANCHOR[align]
        block = self.construct("/Script/UMG.TextBlock")
        block.SetJustification(JUSTIFY[align])
        block.SetRenderTransformPivot(vec2(anchor, 0.5))
        block.SetRenderScale(vec2(size * self.scale, size * self.scale))
        block.SetShadowOffset(vec2(1.5, 1.5))
        block.SetShadowColorAndOpacity(linear(SHADOW))
        slot = block.Slot
        slot.SetAutoSize(True)
        slot.SetAlignment(vec2(anchor, 0.5))
        slot.SetPosition(vec2(*self.pos(x, y)))
        slot.SetZOrder(z)
        return block


class WidgetCache:
    """Calls widget setters only when the value changed, to keep per-frame work down."""

    def __init__(self) -> None:
        self._values: dict[tuple[int, str], Any] = {}

    def set(self, widget: UObject, key: str, value: Any, setter: Callable[[Any], Any]) -> None:
        cache_key = (id(widget), key)
        if self._values.get(cache_key) != value:
            setter(value)
            self._values[cache_key] = value

    def text(self, widget: UObject, text: str) -> None:
        self.set(widget, "text", text, widget.SetText)

    def color(self, widget: UObject, color: RGBA) -> None:
        self.set(widget, "color", color, lambda c: widget.SetColorAndOpacity(slate(c)))

    def brush(self, widget: UObject, color: RGBA) -> None:
        self.set(widget, "brush", color, lambda c: widget.SetBrushColor(linear(c)))

    def clear(self) -> None:
        self._values.clear()


def apply_reels(cache: WidgetCache, reels: list[tuple[UObject, UObject, UObject]], view: OverlayView) -> None:
    """Draws a view's reels onto three columns of (above, payline, below) text blocks."""
    for rows, reel in zip(reels, view.reels, strict=True):
        payline_alpha = 1.0 if reel.stopped else SPINNING_SYMBOL_ALPHA
        for block, symbol, alpha in (
            (rows[0], reel.above, EDGE_SYMBOL_ALPHA),
            (rows[1], reel.payline, payline_alpha),
            (rows[2], reel.below, EDGE_SYMBOL_ALPHA),
        ):
            r, g, b, a = SYMBOL_COLORS[symbol]
            cache.text(block, SYMBOL_LABELS[symbol])
            cache.color(block, (r, g, b, a * alpha))


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
        self._cache = WidgetCache()
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

        root, canvas, screen = new_root(pc, VIEWPORT_Z)
        with removed_on_failure(root):
            return self._fill(root, canvas, screen, user_scale, position)

    def _fill(
        self, root: UObject, canvas: UObject, screen: Screen, user_scale: float, position: str
    ) -> _Widgets:
        scale = screen.fit(user_scale)
        center_x = screen.width / 2
        match position:
            case "Center":
                center_y = screen.height / 2
            case "Bottom":
                center_y = screen.height - POSITION_MARGINS["Bottom"] * scale
            case _:
                center_y = POSITION_MARGINS["Top"] * scale

        ui = Builder(root, canvas, (center_x, center_y), scale)
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
        self._cache.text(widgets.title, view.title)
        self._cache.color(widgets.title, GOLD)
        apply_reels(self._cache, widgets.reels, view)
        self._cache.brush(widgets.payline, PAYLINE_COLORS.get(view.tone, PAYLINE_DEFAULT))
        self._cache.text(widgets.status, view.status)
        self._cache.color(widgets.status, TONE_COLORS[view.tone])
        self._cache.text(widgets.footer, view.footer)
        self._cache.color(widgets.footer, FOOTER_COLOR)


class UmgPrompt:
    """A small hint just below the crosshair, e.g. "[E] PLAY LOOT SLOTS"."""

    def __init__(self) -> None:
        self._root: WeakPointer = WeakPointer()
        self._label: UObject | None = None
        self._text: str | None = None
        self._reported_failure = False

    def show(self, text: str) -> None:
        if text == self._text and self._root() is not None:
            return
        try:
            root = self._root()
            if root is None or self._label is None:
                root = self._build()
            assert self._label is not None
            self._label.SetText(text)
            root.SetVisibility(HIT_TEST_INVISIBLE)
            self._text = text
        except Exception as ex:  # noqa: BLE001 - never let drawing break the game
            if not self._reported_failure:
                logging.error(f"[Borderlands Gamble] Couldn't draw the prompt: {ex!r}")
                self._reported_failure = True

    def hide(self) -> None:
        if self._text is None:
            return
        self._text = None
        root = self._root()
        if root is not None:
            try:
                root.SetVisibility(COLLAPSED)
            except Exception as ex:  # noqa: BLE001
                logging.dev_warning(f"[Borderlands Gamble] Couldn't hide the prompt: {ex!r}")

    def destroy(self) -> None:
        root = self._root()
        if root is not None:
            try:
                root.RemoveFromParent()
            except Exception as ex:  # noqa: BLE001
                logging.dev_warning(f"[Borderlands Gamble] Couldn't remove the prompt: {ex!r}")
        self._root = WeakPointer()
        self._label = None
        self._text = None

    def _build(self) -> UObject:
        self.destroy()
        pc = get_pc()
        if pc is None:
            raise RuntimeError("No player controller to draw for")
        root, canvas, screen = new_root(pc, PROMPT_Z)
        with removed_on_failure(root):
            ui = Builder(root, canvas, (screen.width / 2, screen.height * PROMPT_Y), screen.fit())
            ui.box(-PROMPT_W / 2 - 2, -PROMPT_H / 2 - 2, PROMPT_W + 4, PROMPT_H + 4, GOLD, 0)
            ui.box(-PROMPT_W / 2, -PROMPT_H / 2, PROMPT_W, PROMPT_H, PROMPT_BG, 1)
            label = ui.text(0, 0, PROMPT_SCALE, 2)
            label.SetColorAndOpacity(slate(GOLD))
        self._root = WeakPointer(root)
        self._label = label
        return root


@dataclass
class _SpectatorPanel:
    canvas: UObject
    title: UObject
    symbols: tuple[UObject, UObject, UObject]
    status: UObject


class UmgSpectators:
    """Draws other players' spins as small reels floating above their heads (see `spectate.py`)."""

    def __init__(self) -> None:
        self._root: WeakPointer = WeakPointer()
        self._panels: list[_SpectatorPanel] = []
        self._screen = Screen(DESIGN_W, DESIGN_H, 1.0)
        self._scale = 1.0
        self._cache = WidgetCache()
        self._visible = False
        self._reported_failure = False

    def draw(self, spins: Sequence[tuple[WatchedSpin, tuple[float, float] | None]]) -> None:
        """
        Draws other players' spins.

        Args:
            spins: Each spin, with the point on screen (in pixels) just above that player's head, or
                   None to show it in the corner instead.
        """
        if not spins:
            self.hide()
            return
        try:
            root = self._ensure_built()
            for index, panel in enumerate(self._panels):
                shown = index < len(spins)
                self._cache.set(
                    panel.canvas,
                    "visibility",
                    shown,
                    lambda value, canvas=panel.canvas: canvas.SetVisibility(
                        HIT_TEST_INVISIBLE if value else COLLAPSED
                    ),
                )
                if shown:
                    spin, point = spins[index]
                    self._apply(panel, spin)
                    self._place(panel, point, index)
            if not self._visible:
                root.SetVisibility(HIT_TEST_INVISIBLE)
                self._visible = True
            self._reported_failure = False
        except Exception as ex:  # noqa: BLE001 - never let drawing break the game
            if not self._reported_failure:
                logging.error(f"[Borderlands Gamble] Couldn't draw another player's spin: {ex!r}")
                self._reported_failure = True

    def hide(self) -> None:
        if not self._visible:
            return
        self._visible = False
        root = self._root()
        if root is not None:
            try:
                root.SetVisibility(COLLAPSED)
            except Exception as ex:  # noqa: BLE001
                logging.dev_warning(f"[Borderlands Gamble] Couldn't hide other players' spins: {ex!r}")

    def destroy(self) -> None:
        root = self._root()
        if root is not None:
            try:
                root.RemoveFromParent()
            except Exception as ex:  # noqa: BLE001
                logging.dev_warning(f"[Borderlands Gamble] Couldn't remove other players' spins: {ex!r}")
        self._root = WeakPointer()
        self._panels = []
        self._cache.clear()
        self._visible = False

    def _apply(self, panel: _SpectatorPanel, spin: WatchedSpin) -> None:
        self._cache.text(panel.title, f"{spin.name.upper()}  -  {spin.machine}")
        self._cache.color(panel.title, GOLD)
        for block, reel in zip(panel.symbols, spin.reels, strict=True):
            r, g, b, a = SYMBOL_COLORS[reel.payline]
            self._cache.text(block, SYMBOL_LABELS[reel.payline])
            self._cache.color(block, (r, g, b, a * (1.0 if reel.stopped else SPINNING_SYMBOL_ALPHA)))
        self._cache.text(panel.status, spin.status)
        self._cache.color(panel.status, TONE_COLORS[spin.tone])

    def _place(self, panel: _SpectatorPanel, point: tuple[float, float] | None, index: int) -> None:
        screen, scale = self._screen, self._scale
        width, height = SPECTATOR_W * scale, SPECTATOR_H * scale
        if point is None:
            x, y = DOCK_X * scale, (DOCK_Y + index * (SPECTATOR_H + DOCK_GAP)) * scale
        else:
            # Centred just above the head, but kept on screen
            head_x, head_y = point[0] / screen.dpi, point[1] / screen.dpi
            x = min(max(head_x - width / 2, 0.0), screen.width - width)
            y = min(max(head_y - height, 0.0), screen.height - height)
        position = (round(x), round(y))
        self._cache.set(panel.canvas, "position", position, lambda p: panel.canvas.Slot.SetPosition(vec2(*p)))

    def _ensure_built(self) -> UObject:
        root = self._root()
        if root is not None and self._panels:
            return root
        self.destroy()
        pc = get_pc()
        if pc is None:
            raise RuntimeError("No player controller to draw for")
        root, canvas, screen = new_root(pc, SPECTATOR_Z)
        with removed_on_failure(root):
            scale = screen.fit()
            outer = Builder(root, canvas, (0.0, 0.0), 1.0)
            panels = []
            half_w, half_h = SPECTATOR_W / 2, SPECTATOR_H / 2
            for _ in range(SPECTATOR_PANELS):
                panel_canvas = outer.construct("/Script/UMG.CanvasPanel", COLLAPSED)
                slot = panel_canvas.Slot
                slot.SetAutoSize(False)
                slot.SetSize(vec2(SPECTATOR_W * scale, SPECTATOR_H * scale))
                slot.SetPosition(vec2(0.0, 0.0))
                slot.SetZOrder(0)
                ui = Builder(root, panel_canvas, (half_w * scale, half_h * scale), scale)
                ui.box(-half_w - 2, -half_h - 2, SPECTATOR_W + 4, SPECTATOR_H + 4, GOLD, 0)
                ui.box(-half_w, -half_h, SPECTATOR_W, SPECTATOR_H, SPECTATOR_BG, 1)
                panels.append(
                    _SpectatorPanel(
                        canvas=panel_canvas,
                        title=ui.text(0, -half_h + 20, 0.62, 2),
                        symbols=(ui.text(-115, -2, 0.9, 2), ui.text(0, -2, 0.9, 2), ui.text(115, -2, 0.9, 2)),
                        status=ui.text(0, half_h - 22, 0.66, 2),
                    ),
                )
        self._root = WeakPointer(root)
        self._panels = panels
        self._screen = screen
        self._scale = scale
        self._cache.clear()
        self._visible = False
        return root


def screen_size(pc: UObject) -> Screen:
    """Gets the viewport size in DPI-scaled units, falling back to 1080p."""
    try:
        lib = ui_class("/Script/UMG.WidgetLayoutLibrary").ClassDefaultObject
        size = lib.GetViewportSize(pc)
        dpi = float(lib.GetViewportScale(pc)) or 1.0
        width, height = float(size.X) / dpi, float(size.Y) / dpi
        if width >= 640 and height >= 360:
            return Screen(width, height, dpi)
    except Exception as ex:  # noqa: BLE001
        logging.dev_warning(f"[Borderlands Gamble] Couldn't read viewport size: {ex!r}")
    return Screen(DESIGN_W, DESIGN_H, 1.0)
