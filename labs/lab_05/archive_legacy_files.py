from __future__ import annotations

import argparse
from pathlib import Path


OVERLAID_ANSWER_PATHS = (
    Path("labs/lab_05/evals/tasks.jsonl"),
    Path("labs/lab_05/evals/graders.py"),
    Path("labs/lab_05/reports/failure_analysis_template.md"),
)
LEGACY_SRC_PATHS = (
    Path("labs/lab_05/src/live_agent.py"),
    Path("labs/lab_05/src/known_failure.py"),
)
LEGACY_PATHS = (*OVERLAID_ANSWER_PATHS, *LEGACY_SRC_PATHS)
BACKUP_DIR = Path("labs/lab_05/legacy-backup")
CURRENT_LAB_5_MARKER = Path("labs/lab_05/evals/response_contract.py")


def _available_backup_dir(root: Path) -> Path:
    candidate = root / BACKUP_DIR / "pre-issue-41"
    suffix = 2
    while candidate.exists():
        candidate = root / BACKUP_DIR / f"pre-issue-41-{suffix}"
        suffix += 1
    return candidate


def archive_legacy_files(root: Path | None = None) -> list[tuple[Path, Path]]:
    root = (root or Path.cwd()).resolve()
    candidates = LEGACY_SRC_PATHS if (root / CURRENT_LAB_5_MARKER).is_file() else LEGACY_PATHS
    sources = [relative_path for relative_path in candidates if (root / relative_path).is_file()]
    if not sources:
        return []

    backup_dir = _available_backup_dir(root)
    archived: list[tuple[Path, Path]] = []

    for relative_path in sources:
        source = root / relative_path
        destination = backup_dir / relative_path.relative_to("labs/lab_05")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
        archived.append((relative_path, destination.relative_to(root)))

    return archived


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Archive answer-bearing files from the pre-Issue-41 Lab 5 package."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    current_overlay_present = (args.root.resolve() / CURRENT_LAB_5_MARKER).is_file()
    archived = archive_legacy_files(args.root)
    if not archived:
        print("No pre-Issue-41 Lab 5 files found; nothing was changed.")
        return
    if current_overlay_present:
        paths = ", ".join(path.as_posix() for path in OVERLAID_ANSWER_PATHS)
        print(
            "WARNING: A current Lab 5 overlay is already present. Remaining legacy src files "
            f"will be archived, but student answers at {paths} may already have been overwritten. "
            "Restore them from your own backup or old workspace."
        )
    for source, destination in archived:
        print(f"Archived {source.as_posix()} -> {destination.as_posix()}")


if __name__ == "__main__":
    main()
