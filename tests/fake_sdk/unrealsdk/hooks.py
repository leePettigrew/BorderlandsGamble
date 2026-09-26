from collections.abc import Callable, Iterator
from contextlib import contextmanager
from enum import Enum, auto
from typing import Any


class Type(Enum):
    PRE = auto()
    POST = auto()
    POST_UNCONDITIONAL = auto()


class Block:
    pass


class Unset:
    pass


# (function path, type) -> {identifier: callback}
HOOKS: dict[tuple[str, Type], dict[str, Callable[..., Any]]] = {}


def add_hook(func: str, type: Type, identifier: str, callback: Callable[..., Any]) -> None:
    HOOKS.setdefault((func, type), {})[identifier] = callback


def has_hook(func: str, type: Type, identifier: str) -> bool:
    return identifier in HOOKS.get((func, type), {})


def remove_hook(func: str, type: Type, identifier: str) -> bool:
    return HOOKS.get((func, type), {}).pop(identifier, None) is not None


def inject_next_call() -> None:
    pass


def log_all_calls(should_log: bool) -> None:
    pass


@contextmanager
def prevent_hooking_direct_calls() -> Iterator[None]:
    yield


def fire(func: str, type: Type = Type.POST) -> int:
    """Test helper: calls every hook on a function, like the engine calling it. Returns how many."""
    callbacks = list(HOOKS.get((func, type), {}).values())
    for callback in callbacks:
        callback(None, None, None, None)
    return len(callbacks)
