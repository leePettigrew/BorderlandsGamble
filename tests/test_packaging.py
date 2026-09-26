import importlib.util
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("build_sdkmod", REPO / "tools" / "build_sdkmod.py")
assert _spec is not None and _spec.loader is not None
build_sdkmod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_sdkmod)


class PackagingTests(unittest.TestCase):
    def test_build_is_a_valid_sdkmod(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = build_sdkmod.build(Path(tmp))
            build_sdkmod.validate(output)
            self.assertEqual(output.name, "borderlands_gamble.sdkmod")

            names = zipfile.ZipFile(output).namelist()
            self.assertTrue(all(name.startswith("borderlands_gamble/") for name in names))
            self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))
            for module in ("__init__.py", "sdk_mod.py", "slots.py", "bl4.py", "overlay.py"):
                self.assertIn(f"borderlands_gamble/{module}", names)

    def test_validate_rejects_bad_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "borderlands_gamble.sdkmod"
            with zipfile.ZipFile(bad, "w") as zf:
                zf.writestr("__init__.py", "")
            with self.assertRaises(ValueError):
                build_sdkmod.validate(bad)

            misnamed = Path(tmp) / "Borderlands Gamble.sdkmod"
            with zipfile.ZipFile(misnamed, "w") as zf:
                zf.writestr("borderlands_gamble/__init__.py", "")
                zf.writestr("borderlands_gamble/pyproject.toml", "")
            with self.assertRaises(ValueError):
                build_sdkmod.validate(misnamed)


class ModMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        with (REPO / "src" / "borderlands_gamble" / "pyproject.toml").open("rb") as file:
            self.pyproject = tomllib.load(file)

    def test_sdkmod_fields(self) -> None:
        sdkmod = self.pyproject["tool"]["sdkmod"]
        self.assertEqual(sdkmod["supported_games"], ["BL4"])
        self.assertEqual(sdkmod["coop_support"], "RequiresAllPlayers")
        self.assertEqual(sdkmod["version"], self.pyproject["project"]["version"])

    def test_version_parses_like_mods_base(self) -> None:
        # mods_base's default parser: dot separated ints
        version = self.pyproject["project"]["version"]
        self.assertTrue(all(part.isdigit() for part in version.split(".")))


if __name__ == "__main__":
    unittest.main()
