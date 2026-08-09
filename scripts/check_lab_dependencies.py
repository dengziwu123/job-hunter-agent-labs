from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED_PATHS = [
    'labs/shared',
    'scripts/reset_lab.py',
    'pyproject.toml',
    'uv.lock',
    'labs/lab_01/stage.json',
    'labs/lab_02/stage.json',
    'labs/lab_03/stage.json'
]
STAGE_MANIFESTS = [
    'labs/lab_01/stage.json',
    'labs/lab_02/stage.json',
    'labs/lab_03/stage.json'
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    missing = [path for path in REQUIRED_PATHS if not (root / path).exists()]
    for manifest in STAGE_MANIFESTS:
        manifest_path = root / manifest
        if not manifest_path.is_file():
            continue
        try:
            stage = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(
                "This Lab 03 package appears incomplete. "
                "Invalid stage manifest " + manifest + ": " + str(exc) + ". "
                "Re-extract the Lab packages into the same workspace and run this check again."
            ) from None
        missing.extend(
            (Path(manifest).parent / marker).as_posix()
            for marker in stage.get("installation_markers", [])
            if not (manifest_path.parent / marker).exists()
        )
    if missing:
        raise SystemExit(
            "This Lab 03 package must be extracted into the same workspace "
            "used for the previous labs. Missing: " + ", ".join(missing)
        )
    print("Lab 03 dependency check passed.")


if __name__ == "__main__":
    main()
