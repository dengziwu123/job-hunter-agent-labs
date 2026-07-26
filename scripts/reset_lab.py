from __future__ import annotations

import argparse
import shutil
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
VALID_LABS = {f"lab_{index:02d}" for index in range(1, 8)}


def reset_lab(lab: str) -> None:
    if lab not in VALID_LABS:
        raise SystemExit(f"Unknown lab: {lab}. Expected one of: {', '.join(sorted(VALID_LABS))}")

    artifact_dir = ROOT_DIR / "artifacts" / lab
    artifacts_root = (ROOT_DIR / "artifacts").resolve()
    resolved_artifact_dir = artifact_dir.resolve()
    if resolved_artifact_dir.parent != artifacts_root:
        raise SystemExit(f"Refusing to reset outside artifacts/: {lab}")

    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / ".gitkeep").write_text("", encoding="utf-8")

    print(f"reset={lab}")
    print("preserved=.env")
    print(f"cleared={artifact_dir}")
    print("note=This script only clears artifacts. Restore code files from git or a fresh template copy.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("lab")
    args = parser.parse_args()
    reset_lab(args.lab)


if __name__ == "__main__":
    main()
