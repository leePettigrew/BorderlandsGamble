import sys
from pathlib import Path

# Make the mod package importable without installing it. Run tests from the repo root with
# `python -m unittest` (or `python -m pytest`).
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
