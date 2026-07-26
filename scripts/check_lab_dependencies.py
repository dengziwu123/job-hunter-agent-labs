from __future__ import annotations

import argparse
from pathlib import Path


REQUIRED_PATHS = [
    'labs/shared',
    'scripts/reset_lab.py',
    'pyproject.toml',
    'uv.lock',
    'labs/lab_01',
    'labs/lab_02'
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    missing = [path for path in REQUIRED_PATHS if not (root / path).exists()]
    if missing:
        raise SystemExit(
            "This Lab 02 package must be extracted into the same workspace "
            "used for the previous labs. Missing: " + ", ".join(missing)
        )
    print("Lab 02 dependency check passed.")


if __name__ == "__main__":
    main()
