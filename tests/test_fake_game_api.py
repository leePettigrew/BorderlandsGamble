"""
Keeps the fake game honest. Its player controller, character, and player state may only have the
fields and functions that real BL4 objects have, so a fake session can't pass on a call the game
doesn't support. That's how `K2_GetActorTransform`, which no actor has, once got through.
"""

import ast
import json
import re
import unittest
from pathlib import Path

FAKE_SDK = Path(__file__).resolve().parent / "fake_sdk"
REAL_NAMES = json.loads((FAKE_SDK / "bl4_names.json").read_text())
# Fake class -> the real object it stands in for
FAKES = {"PlayerController": "PlayerController", "Pawn": "OakCharacter", "PlayerState": "PlayerState"}
# The game's names are PascalCase, K2_ functions, or bFlags. Anything else is a test helper
GAME_NAME = re.compile(r"^(K2_|b[A-Z]|[A-Z])")


def members(cls: ast.ClassDef) -> set[str]:
    """Gets the methods, class attributes, and `self.` attributes a class defines."""
    names = set()
    for node in cls.body:
        if isinstance(node, ast.FunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {target.id for target in node.targets if isinstance(target, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    for node in ast.walk(cls):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            names.add(node.attr)
    return names


class FakeGameApiTests(unittest.TestCase):
    def test_fakes_only_have_real_names(self) -> None:
        tree = ast.parse((FAKE_SDK / "fake_game.py").read_text())
        classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        shared = members(classes["FakeObject"])
        for fake, real in FAKES.items():
            with self.subTest(fake=fake):
                real_names = {name.lower() for name in REAL_NAMES[real]}
                invented = sorted(
                    name
                    for name in members(classes[fake]) | shared
                    if GAME_NAME.match(name) and name.lower() not in real_names
                )
                self.assertEqual(invented, [], f"the fake {fake} has names a real {real} doesn't")

    def test_real_names_are_real(self) -> None:
        real_names = {name.lower() for name in REAL_NAMES["OakCharacter"]}
        self.assertIn("k2_getactorlocation", real_names)
        self.assertIn("gettransform", real_names)
        self.assertNotIn("k2_getactortransform", real_names)


if __name__ == "__main__":
    unittest.main()
