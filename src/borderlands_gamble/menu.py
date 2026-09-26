"""
The slot machine menu: the reels, the buttons, the paytable, and your wallet, on one screen.

It's built at runtime from UMG widgets, like the overlay. Clicks work the way Matt's BL4 Mods Menu
does it, which is proven in game: real `Button` widgets made almost fully transparent, over our own
backgrounds, with clicks read by polling `IsPressed` (plus the raw mouse, as a fallback). Keys are
polled too, since keybinds can't be relied on while a menu has focus.

While it's open, the menu shows the mouse cursor, stops the player moving and looking around, and
puts the game's UI in its "cinematic" state, which hides the HUD. Closing puts all of that back.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

import unrealsdk
from unrealsdk import logging
from unrealsdk.unreal import UObject, WeakPointer

from . import bl4
from .casino import OverlayView, Tone
from .menu_model import ACT_ON_RELEASE, MENU_KEYS, PANEL_ROWS, KeyWatcher, MenuAction
from .overlay import (
    GOLD,
    PANEL_BG,
    PAYLINE_COLORS,
    PAYLINE_DEFAULT,
    REEL_BG,
    TONE_COLORS,
    VISIBLE,
    Builder,
    WidgetCache,
    apply_reels,
    new_root,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from .menu_model import RGBA, MenuInfo

MENU_Z = 999_500
IDLE_STATUS = "Pull the lever!"

# The game UI state that hides the HUD while the menu is up
UI_STATE = "CINEMATIC"

# Layout, in 1080p units relative to the centre of the screen.
PANEL_W = 1300.0
PANEL_H = 700.0
MACHINE_X = -335.0
REEL_W = 170.0
REEL_H = 210.0
REEL_SPACING = 190.0
ROW_H = 70.0
REELS_TOP = -235.0
PAYTABLE_X = 10.0
ODDS_X = 255.0
PAYS_X = 620.0
PAYTABLE_TOP = -262.0
PAYTABLE_ROW_H = 35.0
PAYTABLE_ROWS = PANEL_ROWS

# (action, x, y, w, h, label scale), with x/y the top left corner. PULL comes first, it gets focus.
BUTTONS: tuple[tuple[MenuAction, float, float, float, float, float], ...] = (
    (MenuAction.PULL, -620.0, 76.0, 570.0, 74.0, 1.05),
    (MenuAction.MACHINE, -620.0, 162.0, 280.0, 54.0, 0.7),
    (MenuAction.LOOT_NEXT, -330.0, 162.0, 280.0, 54.0, 0.7),
    (MenuAction.BET_UP, -620.0, 226.0, 280.0, 54.0, 0.7),
    (MenuAction.LEAVE, -330.0, 226.0, 280.0, 54.0, 0.7),
    # Top right of the right hand panel: switches it between the paytable and the leaderboard
    (MenuAction.BOARD, 420.0, -330.0, 200.0, 46.0, 0.6),
)
# Actions that can't change while a pull is in flight
LOCKED_WHILE_BUSY = frozenset({MenuAction.MACHINE, MenuAction.LOOT_NEXT, MenuAction.BET_UP})

WHITE: RGBA = (0.92, 0.92, 0.92, 1.0)
GREY: RGBA = (0.62, 0.62, 0.62, 1.0)
DIM_GOLD: RGBA = (0.95, 0.72, 0.18, 0.35)
BACKDROP: RGBA = (0.0, 0.0, 0.0, 0.45)
BUTTON_FILL: RGBA = (0.12, 0.10, 0.17, 0.96)
BUTTON_HOVER: RGBA = (0.24, 0.20, 0.32, 0.98)
PULL_FILL: RGBA = (0.52, 0.33, 0.04, 0.96)
PULL_HOVER: RGBA = (0.74, 0.50, 0.09, 0.98)
DISABLED_TEXT: RGBA = (0.45, 0.45, 0.45, 1.0)
# Almost invisible, so our own backgrounds show through, but still there to be clicked
BUTTON_OPACITY = 0.03

MOUSE_BUTTON = "LeftMouseButton"


@dataclass
class _Button:
    action: MenuAction
    hit: UObject
    back: UObject
    label: UObject
    # Where it is in rendered pixels, to compare against the mouse: x, y, w, h
    rect: tuple[float, float, float, float]
    fill: RGBA
    hover: RGBA
    enabled: bool = True
    was_pressed: bool = False
    mouse_was_down: bool = False

    def contains(self, x: float, y: float) -> bool:
        rx, ry, rw, rh = self.rect
        return rx <= x <= rx + rw and ry <= y <= ry + rh


@dataclass
class _Widgets:
    root: UObject
    title: UObject
    tagline: UObject
    reels: list[tuple[UObject, UObject, UObject]]
    payline: UObject
    status: UObject
    wallet: UObject
    hints: UObject
    paytable_title: UObject
    rows: list[tuple[UObject, UObject, UObject]]
    summary: UObject
    note: UObject
    lifetime: UObject
    buttons: list[_Button] = field(default_factory=list)


# Player controller flags the menu turns on while it's open
INPUT_FLAGS = ("bShowMouseCursor", "bEnableMouseOverEvents", "bBlockInput")


@dataclass
class _InputSnapshot:
    """What the menu changed on the player controller, to put back when it closes."""

    flags: dict[str, bool] = field(default_factory=dict)
    ui_state_pushed: bool = False


class SlotMenu:
    """
    The slot machine menu. Also a `casino.Display`, showing the reels while it's open.

    Actions from clicks and keys go to `on_action`, which decides what they do.
    """

    def __init__(self, on_action: Callable[[MenuAction], None], scale: Callable[[], float]) -> None:
        """
        Args:
            on_action: Called with each button clicked or key pressed.
            scale: Gets how big to draw the menu, relative to fitting the screen.
        """
        self.on_action = on_action
        self.scale = scale
        self._root: WeakPointer = WeakPointer()
        self._widgets: _Widgets | None = None
        self._pc: WeakPointer = WeakPointer()
        # The pawn the menu was opened with. Compared by address: the SDK can hand back different
        # Python wrappers for the same object
        self._pawn_address: int | None = None
        self._cache = WidgetCache()
        self._view: OverlayView | None = None
        self._info: MenuInfo | None = None
        self._input = _InputSnapshot()
        self._key_map: dict[str, MenuAction] = dict(MENU_KEYS)
        self._keys = KeyWatcher(self._key_map, ACT_ON_RELEASE)
        self._key_structs: dict[str, object] = {}
        self._reported_key_failure = False

    @property
    def is_open(self) -> bool:
        return self._widgets is not None

    def open(
        self, info: MenuInfo, view: OverlayView, extra_keys: Mapping[str, MenuAction] | None = None
    ) -> bool:
        """
        Opens the menu.

        Args:
            info: What to show around the reels.
            view: What to show on the reels.
            extra_keys: More keys to watch, e.g. the mod's own keybinds, which might not fire while
                        the menu has focus.
        Returns:
            True if it opened.
        """
        if self.is_open:
            return True
        pc = bl4.local_player()
        if pc is None:
            return False
        try:
            self._widgets = self._build(pc)
        except Exception as ex:  # noqa: BLE001 - never leave half a menu around
            logging.error(f"[Borderlands Gamble] Couldn't build the slot machine menu: {ex!r}")
            self._destroy_widgets()
            return False

        self._pc = WeakPointer(pc)
        self._pawn_address = _address(pc.Pawn)
        self._root = WeakPointer(self._widgets.root)
        self._view = None
        self._info = None
        self.set_info(info)
        self.render(view)
        self._widgets.root.SetVisibility(VISIBLE)
        self._capture_input(pc)
        self._key_map = {**MENU_KEYS, **(extra_keys or {})}
        self._keys = KeyWatcher(self._key_map, ACT_ON_RELEASE)
        self._key_structs = {
            key: unrealsdk.make_struct("Key", KeyName=key) for key in (*self._key_map, MOUSE_BUTTON)
        }
        self._keys.prime(self._keys_down(pc, self._key_map))
        return True

    def close(self) -> None:
        """Closes the menu, and gives the player back control."""
        if not self.is_open:
            return
        self._destroy_widgets()
        pc = self._pc()
        if pc is not None:
            self._release_input(pc)
        elif self._input.ui_state_pushed:
            # After a map change (or quitting to the title screen) the controller we changed is gone.
            # Leave the new one's input alone, but the game's UI state may have survived
            current = bl4.local_player()
            if current is not None:
                bl4.run_console_command(current, f"gbx.ui.view.stateremove {UI_STATE}")
        self._input = _InputSnapshot()
        self._pc = WeakPointer()
        self._pawn_address = None

    def check(self) -> bool:
        """
        Closes the menu if the game took it away (e.g. a map change), or the player respawned.

        Returns:
            True if the menu is (still) open.
        """
        if self._widgets is None:
            return False
        pc = self._pc()
        if pc is None or self._root() is None or _address(pc.Pawn) != self._pawn_address:
            self.close()
            return False
        return True

    def set_info(self, info: MenuInfo) -> None:
        """Updates everything around the reels."""
        widgets = self._widgets
        if widgets is None or info == self._info or self._root() is None:
            return
        self._info = info
        cache = self._cache
        cache.text(widgets.tagline, info.tagline)
        cache.text(widgets.wallet, info.wallet)
        cache.text(widgets.hints, info.hints)
        cache.text(widgets.paytable_title, info.paytable_title)
        for idx, (pattern, odds, pays) in enumerate(widgets.rows):
            row = info.paytable[idx] if idx < len(info.paytable) else None
            cache.text(pattern, row.pattern if row else "")
            cache.color(pattern, row.color if row else WHITE)
            cache.text(odds, row.odds if row else "")
            cache.text(pays, row.pays if row else "")
        cache.text(widgets.summary, info.summary)
        cache.text(widgets.note, info.note)
        cache.text(widgets.lifetime, info.lifetime)

        labels = {
            MenuAction.PULL: info.pull_label,
            MenuAction.MACHINE: info.machine_label,
            MenuAction.LOOT_NEXT: info.drops_label,
            MenuAction.BET_UP: info.bet_label,
            MenuAction.LEAVE: "LEAVE",
            MenuAction.BOARD: info.board_label,
        }
        for button in widgets.buttons:
            button.enabled = not (info.busy and button.action in LOCKED_WHILE_BUSY)
            cache.text(button.label, labels[button.action])
            cache.color(button.label, WHITE if button.enabled else DISABLED_TEXT)

    # casino.Display
    def render(self, view: OverlayView) -> None:
        widgets = self._widgets
        # Never touch widgets the game has cleaned up
        if widgets is None or self._root() is None:
            return
        self._view = view
        cache = self._cache
        cache.text(widgets.title, view.title)
        apply_reels(cache, widgets.reels, view)
        cache.brush(widgets.payline, PAYLINE_COLORS.get(view.tone, PAYLINE_DEFAULT))
        cache.text(widgets.status, view.status)
        cache.color(widgets.status, TONE_COLORS[view.tone])

    def hide(self) -> None:
        """Nothing to show any more, so go back to waiting for a pull, reels where they stopped."""
        if self._view is not None:
            self.render(replace(self._view, status=IDLE_STATUS, tone=Tone.INFO, footer=""))

    def tick(self) -> None:
        """Handles clicks and key presses. Should be called every frame while open."""
        if not self.check():
            return
        widgets = self._widgets
        pc = self._pc()
        assert widgets is not None and pc is not None

        # The game sometimes takes the cursor back, e.g. when a HUD element updates
        pc.bShowMouseCursor = True

        actions = self._poll_buttons(pc, widgets.buttons)
        actions += [self._key_map[key] for key in self._keys.update(self._keys_down(pc, self._key_map))]
        for action in actions:
            if not self.is_open:
                break
            self.on_action(action)
        if actions and self.is_open and widgets.buttons:
            # Keep keyboard focus on PULL, so Space, Enter and A always pull
            _try(widgets.buttons[0].hit, "SetKeyboardFocus")

    # ==============================================================================================

    def _keys_down(self, pc: UObject, keys: Iterable[str]) -> list[str]:
        down = []
        for key in keys:
            struct = self._key_structs.get(key) or unrealsdk.make_struct("Key", KeyName=key)
            try:
                if pc.IsInputKeyDown(struct):
                    down.append(key)
            except Exception as ex:  # noqa: BLE001 - that key just never counts as down
                if not self._reported_key_failure:
                    self._reported_key_failure = True
                    logging.dev_warning(f"[Borderlands Gamble] Couldn't read key {key}: {ex!r}")
        return down

    def _poll_buttons(self, pc: UObject, buttons: list[_Button]) -> list[MenuAction]:
        mouse: tuple[float, float] | None = None
        try:
            ok, mouse_x, mouse_y = pc.GetMousePosition(0.0, 0.0)
            if ok:
                mouse = (float(mouse_x), float(mouse_y))
        except Exception:  # noqa: BLE001 - only needed for the fallback
            pass
        mouse_down = bool(self._keys_down(pc, (MOUSE_BUTTON,)))

        clicked: list[MenuAction] = []
        for button in buttons:
            try:
                pressed = bool(button.hit.IsPressed())
                hovered = bool(button.hit.IsHovered())
            except Exception:  # noqa: BLE001
                pressed = hovered = False
            inside = mouse is not None and button.contains(*mouse)

            click = False
            if pressed and not button.was_pressed:
                button.was_pressed = True
            elif button.was_pressed and not pressed:
                button.was_pressed = False
                click = True
            # Fallback, if the game doesn't route clicks to our widgets: the mouse was pressed and
            # let go over the button
            if button.mouse_was_down and not mouse_down and inside:
                click = True
            button.mouse_was_down = mouse_down and inside

            self._cache.brush(
                button.back, button.hover if (hovered or inside) and button.enabled else button.fill
            )
            if click and button.enabled:
                clicked.append(button.action)
        return clicked

    def _capture_input(self, pc: UObject) -> None:
        snapshot = self._input
        for attr in INPUT_FLAGS:
            try:
                previous = bool(getattr(pc, attr))
                setattr(pc, attr, True)
            except Exception as ex:  # noqa: BLE001 - each step is best effort
                logging.dev_warning(f"[Borderlands Gamble] Couldn't set {attr}: {ex!r}")
                continue
            snapshot.flags[attr] = previous
        for func in ("SetIgnoreLookInput", "SetIgnoreMoveInput"):
            _try(pc, func, True)

        snapshot.ui_state_pushed = bl4.run_console_command(pc, f"gbx.ui.view.stateadd {UI_STATE}")

        widgets = self._widgets
        focus = widgets.buttons[0].hit if widgets is not None and widgets.buttons else None
        if focus is not None:
            _try(focus, "SetUserFocus", pc)
            _try(focus, "SetKeyboardFocus")
        library = _widget_library()
        if library is not None:
            # (controller, widget to focus, don't lock the mouse, keep the cursor, don't flush input)
            _try(library, "SetInputMode_GameAndUIEx", pc, focus, 0, False, False)

    def _release_input(self, pc: UObject) -> None:
        library = _widget_library()
        if library is not None:
            _try(library, "SetInputMode_GameOnly", pc, True)
            _try(library, "SetFocusToGameViewport")
        # A full reset rather than undoing our one SetIgnore...(True): whatever else happened while
        # the menu was open, the player must be able to move and look around once it closes
        for func in ("ResetIgnoreLookInput", "ResetIgnoreMoveInput"):
            _try(pc, func)

        snapshot = self._input
        for attr, value in snapshot.flags.items():
            try:
                setattr(pc, attr, value)
            except Exception as ex:  # noqa: BLE001
                logging.dev_warning(f"[Borderlands Gamble] Couldn't restore {attr}: {ex!r}")
        if snapshot.ui_state_pushed:
            bl4.run_console_command(pc, f"gbx.ui.view.stateremove {UI_STATE}")

    def _destroy_widgets(self) -> None:
        root = self._root()
        if root is not None:
            try:
                root.RemoveFromParent()
            except Exception as ex:  # noqa: BLE001
                logging.dev_warning(f"[Borderlands Gamble] Couldn't remove the menu: {ex!r}")
        self._root = WeakPointer()
        self._widgets = None
        self._cache.clear()

    def _build(self, pc: UObject) -> _Widgets:
        root, canvas, screen = new_root(pc, MENU_Z)
        self._root = WeakPointer(root)
        center = (screen.width / 2, screen.height / 2)
        scale = screen.fit(self.scale())
        ui = Builder(root, canvas, center, scale)
        root.SetVisibility(VISIBLE)

        # Dim the game, and catch any clicks that miss the buttons so they don't reach the game
        big = max(screen.width, screen.height) / scale
        ui.box(-big, -big, 2 * big, 2 * big, BACKDROP, 0)
        catcher = ui.construct("/Script/UMG.Button", VISIBLE, _not_focusable)
        ui.place(catcher, -big, -big, 2 * big, 2 * big, 1)
        catcher.SetRenderOpacity(0.0)

        half_w, half_h = PANEL_W / 2, PANEL_H / 2
        ui.box(-half_w - 4, -half_h - 4, PANEL_W + 8, PANEL_H + 8, GOLD, 2)
        ui.box(-half_w, -half_h, PANEL_W, PANEL_H, PANEL_BG, 3)
        ui.box(-20, -half_h + 30, 2, PANEL_H - 60, DIM_GOLD, 4)

        def text(x: float, y: float, size: float, color: RGBA, align: str = "center") -> UObject:
            block = ui.text(x, y, size, 10, align)
            self._cache.color(block, color)
            return block

        reels: list[tuple[UObject, UObject, UObject]] = []
        for idx in range(3):
            reel_x = MACHINE_X + (idx - 1) * REEL_SPACING
            ui.box(reel_x - REEL_W / 2, REELS_TOP, REEL_W, REEL_H, REEL_BG, 5)
            above, payline, below = (
                ui.text(reel_x, REELS_TOP + ROW_H * (row + 0.5), size, 10)
                for row, size in enumerate((1.0, 1.3, 1.0))
            )
            reels.append((above, payline, below))
        band_w = 2 * REEL_SPACING + REEL_W + 10
        payline_band = ui.box(MACHINE_X - band_w / 2, REELS_TOP + ROW_H, band_w, ROW_H, PAYLINE_DEFAULT, 6)

        widgets = _Widgets(
            root=root,
            title=text(MACHINE_X, -half_h + 40, 1.45, GOLD),
            tagline=text(MACHINE_X, -half_h + 80, 0.68, GREY),
            reels=reels,
            payline=payline_band,
            status=text(MACHINE_X, 3, 1.0, WHITE),
            wallet=text(MACHINE_X, 42, 0.78, WHITE),
            hints=text(MACHINE_X, half_h - 30, 0.56, GREY),
            paytable_title=text(PAYTABLE_X, -half_h + 45, 0.9, GOLD, "left"),
            rows=[
                (
                    text(PAYTABLE_X, PAYTABLE_TOP + idx * PAYTABLE_ROW_H, 0.64, WHITE, "left"),
                    text(ODDS_X, PAYTABLE_TOP + idx * PAYTABLE_ROW_H, 0.64, GREY, "left"),
                    text(PAYS_X, PAYTABLE_TOP + idx * PAYTABLE_ROW_H, 0.64, WHITE, "right"),
                )
                for idx in range(PAYTABLE_ROWS)
            ],
            summary=text(PAYTABLE_X, half_h - 118, 0.6, WHITE, "left"),
            note=text(PAYTABLE_X, half_h - 85, 0.6, GREY, "left"),
            lifetime=text(PAYTABLE_X, half_h - 50, 0.6, WHITE, "left"),
        )

        for action, x, y, w, h, label_scale in BUTTONS:
            pull = action is MenuAction.PULL
            fill, hover = (PULL_FILL, PULL_HOVER) if pull else (BUTTON_FILL, BUTTON_HOVER)
            back = ui.box(x, y, w, h, fill, 20)
            # Only PULL takes keyboard focus. Slate presses the focused button on Space, Enter or A
            hit = ui.construct("/Script/UMG.Button", VISIBLE, None if pull else _not_focusable)
            ui.place(hit, x, y, w, h, 21)
            hit.SetRenderOpacity(BUTTON_OPACITY)
            label = ui.text(x + w / 2, y + h / 2, label_scale, 22)
            left, top = ui.pos(x, y)
            rect = (left * screen.dpi, top * screen.dpi, w * scale * screen.dpi, h * scale * screen.dpi)
            widgets.buttons.append(_Button(action, hit, back, label, rect, fill, hover))
        return widgets


def _not_focusable(button: UObject) -> None:
    # Only read when the button's Slate widget gets built, i.e. before it's added to the canvas
    try:
        button.IsFocusable = False
    except Exception as ex:  # noqa: BLE001
        logging.dev_warning(f"[Borderlands Gamble] Couldn't make a button unfocusable: {ex!r}")


def _widget_library() -> UObject | None:
    try:
        return unrealsdk.find_class("WidgetBlueprintLibrary").ClassDefaultObject
    except Exception as ex:  # noqa: BLE001
        logging.dev_warning(f"[Borderlands Gamble] Couldn't find WidgetBlueprintLibrary: {ex!r}")
        return None


def _address(obj: UObject | None) -> int | None:
    return None if obj is None else int(obj._get_address())


def _try(obj: UObject, func: str, *args: object) -> None:
    """Calls a function that isn't essential, logging rather than raising if it fails."""
    try:
        getattr(obj, func)(*args)
    except Exception as ex:  # noqa: BLE001
        logging.dev_warning(f"[Borderlands Gamble] {func} failed: {ex!r}")
