from collections.abc import Callable
from typing import Any

NEXT_LINE = object()

COMMANDS: dict[str, Callable[[str, int], Any]] = {}


def add_command(cmd: Any, callback: Callable[[str, int], Any]) -> bool:
    COMMANDS[str(cmd).lower()] = callback
    return True


def has_command(cmd: Any) -> bool:
    return str(cmd).lower() in COMMANDS


def remove_command(cmd: Any) -> bool:
    return COMMANDS.pop(str(cmd).lower(), None) is not None


def run(line: str) -> None:
    """Test helper: runs a console command line."""
    name = line.split()[0]
    COMMANDS[name.lower()](line, len(name))
