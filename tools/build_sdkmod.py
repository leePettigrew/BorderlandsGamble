"""
Packages the mod into a `.sdkmod` - a zip holding a single folder named after the file.

Usage: python tools/build_sdkmod.py [--out DIR]
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MOD_NAME = "borderlands_gamble"
MOD_DIR = REPO / "src" / MOD_NAME

EXCLUDED_DIRS = {"__pycache__"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def mod_files(mod_dir: Path = MOD_DIR) -> list[Path]:
    """Lists every file that belongs in the package, in a stable order."""
    return sorted(
        path
        for path in mod_dir.rglob("*")
        if path.is_file()
        and not EXCLUDED_DIRS.intersection(path.relative_to(mod_dir).parts)
        and path.suffix not in EXCLUDED_SUFFIXES
    )


def build(out_dir: Path, mod_dir: Path = MOD_DIR) -> Path:
    """
    Builds the `.sdkmod`.

    Args:
        out_dir: The folder to write it to.
        mod_dir: The mod folder to package.
    Returns:
        The path to the built file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"{mod_dir.name}.sdkmod"

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Explicit directory entry, so the single root folder is obvious to every zip reader
        zf.writestr(f"{mod_dir.name}/", "")
        for path in mod_files(mod_dir):
            zf.write(path, f"{mod_dir.name}/{path.relative_to(mod_dir).as_posix()}")
    return output


def validate(sdkmod: Path) -> None:
    """
    Checks a `.sdkmod` the same way the mod manager does before importing it.

    Args:
        sdkmod: The file to check.
    """
    root = list(zipfile.Path(sdkmod).iterdir())
    if len(root) != 1 or root[0].name != sdkmod.stem:
        raise ValueError(f"{sdkmod.name} must contain exactly one root folder, named '{sdkmod.stem}'")
    names = zipfile.ZipFile(sdkmod).namelist()
    for required in ("__init__.py", "pyproject.toml"):
        if f"{sdkmod.stem}/{required}" not in names:
            raise ValueError(f"{sdkmod.name} is missing {required}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--out", type=Path, default=REPO / "dist", help="Output folder.")
    args = parser.parse_args()

    output = build(args.out)
    validate(output)
    print(f"Built {output}")


if __name__ == "__main__":
    main()
