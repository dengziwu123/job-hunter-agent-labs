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
    'labs/lab_03/stage.json',
    'labs/lab_04/stage.json'
]
STAGE_MANIFESTS = [
    'labs/lab_01/stage.json',
    'labs/lab_02/stage.json',
    'labs/lab_03/stage.json',
    'labs/lab_04/stage.json'
]
LAB_4_REWORK_REQUIREMENTS = {
    'labs/lab_04/src/context_budget.py': 'def select_context(',
    'labs/lab_04/src/mcp_client_adapter.py': 'class MCPBoundaryError',
    'labs/lab_04/src/prompt_loader.py': 'REQUIRED_PLACEHOLDERS',
    'labs/lab_04/prompts/grounded-job-research.md': '{{job_openings}}',
    'pyproject.toml': '"mcp>=2.0"',
    'uv.lock': 'name = "mcp"'
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    missing = [path for path in REQUIRED_PATHS if not (root / path).exists()]
    outdated_lab_4 = []
    for relative_path, marker in LAB_4_REWORK_REQUIREMENTS.items():
        path = root / relative_path
        try:
            current = path.read_text(encoding="utf-8")
        except OSError:
            outdated_lab_4.append(relative_path)
            continue
        if marker not in current:
            outdated_lab_4.append(relative_path)
    if outdated_lab_4:
        raise SystemExit(
            "This workspace does not contain the current Lab 4 runtime and MCP dependencies. "
            "The cumulative browser patch intentionally does not overwrite Lab 4 student work. "
            "Install the current Lab 4 package into this workspace, complete its task-prompt TODO, "
            "run uv sync, and then continue. Outdated or missing: " + ", ".join(outdated_lab_4)
        )
    for manifest in STAGE_MANIFESTS:
        manifest_path = root / manifest
        if not manifest_path.is_file():
            continue
        try:
            stage = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(
                "This Lab 04 package appears incomplete. "
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
            "This Lab 04 package must be extracted into the same workspace "
            "used for the previous labs. Missing: " + ", ".join(missing)
        )
    print("Lab 04 dependency check passed.")


if __name__ == "__main__":
    main()
