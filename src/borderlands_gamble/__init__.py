"""Borderlands Gamble - slot machines for Borderlands 4, as a PythonSDK mod."""

try:
    from unrealsdk import find_object as _  # noqa: F401 - only the real SDK provides this
except ImportError:
    # Not running inside the game (e.g. unit tests, or the tools in this repo), so this package is
    # just the pure slot machine engine.
    pass
else:
    from .sdk_mod import mod as mod
