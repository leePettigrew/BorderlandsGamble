"""Prints the exact odds of every machine at every luck preset. Handy while tuning reel weights."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from borderlands_gamble.machines import LUCK_PRESETS, MACHINES
from borderlands_gamble.report import odds_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--machine", choices=sorted(MACHINES), help="Only show this machine.")
    parser.add_argument("--luck", choices=list(LUCK_PRESETS), help="Only show this luck preset.")
    parser.add_argument("--level", type=int, default=50, help="Player level to quote prices at.")
    args = parser.parse_args()

    for key, machine in MACHINES.items():
        if args.machine and key != args.machine:
            continue
        for luck in LUCK_PRESETS:
            if args.luck and luck != args.luck:
                continue
            print("\n".join(odds_report(machine, luck, level=args.level)))
            print()


if __name__ == "__main__":
    main()
