from __future__ import annotations

from typing import Any


class UObject:
    """Base for every fake game object. Unknown attributes raise, like the real SDK."""

    Name: str = "Object"

    def _get_address(self) -> int:
        return id(self)

    def _path_name(self) -> str:
        return self.Name


class UClass(UObject):
    pass


class UScriptStruct(UObject):
    def __init__(self, name: str) -> None:
        self.Name = name


class BoundFunction:
    pass


class WrappedStruct:
    def __init__(self, struct_name: str, **fields: Any) -> None:
        self._struct_name = struct_name
        for key, value in fields.items():
            setattr(self, key, value)

    def __repr__(self) -> str:
        fields = ", ".join(f"{k}={v!r}" for k, v in vars(self).items() if not k.startswith("_"))
        return f"{self._struct_name}({fields})"


IGNORE_STRUCT = object()


class WeakPointer:
    """Holds a reference until the fake game marks the object as garbage collected."""

    def __init__(self, obj: UObject | None = None) -> None:
        self._obj = obj

    def __call__(self) -> UObject | None:
        if self._obj is None or getattr(self._obj, "_collected", False):
            return None
        return self._obj

    def replace(self, obj: UObject | None) -> None:
        self._obj = obj


class FGbxDefPtr:
    def __init__(self, name: str, type: Any = None, fully_qualified: bool | None = None) -> None:
        self._name = name
        self._type = type

    def __repr__(self) -> str:
        return f"FGbxDefPtr({self._name!r})"


class FGameDataHandle:
    def __init__(self, type_handle: int, name: str) -> None:
        self._type_handle = type_handle
        self._name = name
