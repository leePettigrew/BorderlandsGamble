"""
A tiny stand-in for the native `unrealsdk` module, backed by the simulated game in `fake_game.py`.

Only implements what mods_base and this mod actually use. Anything else raises, just like asking
the real SDK for a field that doesn't exist, so typos in the mod still fail loudly.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Any

from . import commands, hooks, logging, unreal

__all__ = (
    "commands",
    "config",
    "construct_object",
    "find_all",
    "find_class",
    "find_enum",
    "find_object",
    "hooks",
    "load_package",
    "logging",
    "make_struct",
    "unreal",
)

__version__ = "unrealsdk 0.0.0-fake"
config: dict[str, Any] = {}


class EInputEvent(Enum):
    IE_Pressed = auto()
    IE_Released = auto()
    IE_Repeat = auto()
    IE_DoubleClick = auto()
    IE_Axis = auto()
    IE_MAX = auto()


def _game() -> Any:
    import fake_game

    return fake_game.GAME


def _class_name(cls: Any) -> str:
    return cls if isinstance(cls, str) else cls.Name


def find_object(cls: Any, name: str) -> Any:
    return _game().find_object(_class_name(cls), name)


def find_all(cls: Any, exact: bool = True) -> list[Any]:
    return _game().find_all(_class_name(cls), exact)


def find_class(name: str, fully_qualified: bool | None = None) -> Any:
    return _game().find_class(name)


def find_enum(name: str, fully_qualified: bool | None = None) -> Any:
    if name == "EInputEvent":
        return EInputEvent
    raise ValueError(f"No enum {name}")


def make_struct(name: str, fully_qualified: bool | None = None, /, **kwargs: Any) -> Any:
    return unreal.WrappedStruct(name, **kwargs)


def construct_object(
    cls: Any,
    outer: Any,
    name: str = "None",
    flags: int = 0,
    template_obj: Any = None,
) -> Any:
    return _game().construct(cls, outer)


def load_package(name: str, flags: int = 0) -> Any:
    raise ValueError(f"Can't load {name} in the fake SDK")
