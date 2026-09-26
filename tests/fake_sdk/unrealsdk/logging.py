from typing import Any

LINES: list[tuple[str, str]] = []


def _log(level: str, *args: Any) -> None:
    LINES.append((level, " ".join(str(arg) for arg in args)))


def is_console_ready() -> bool:
    return True


def info(*args: Any) -> None:
    _log("info", *args)


def warning(*args: Any) -> None:
    _log("warning", *args)


def error(*args: Any) -> None:
    _log("error", *args)


def dev_warning(*args: Any) -> None:
    _log("dev_warning", *args)


def misc(*args: Any) -> None:
    _log("misc", *args)
