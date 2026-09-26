import os
import subprocess
import sys
import unittest
from pathlib import Path

RUNNER = Path(__file__).resolve().parent / "fake_sdk" / "run_session.py"
MODS_BASE_DIR = os.environ.get("MODS_BASE_DIR", "")


@unittest.skipUnless(sys.version_info >= (3, 14), "mods_base needs Python 3.14, like the SDK")
@unittest.skipUnless(
    MODS_BASE_DIR and (Path(MODS_BASE_DIR) / "__init__.py").exists(),
    "set MODS_BASE_DIR to a checkout of https://github.com/bl-sdk/mods_base",
)
class SdkSessionTests(unittest.TestCase):
    def test_session_in_fake_game(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(RUNNER), MODS_BASE_DIR],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Session OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
