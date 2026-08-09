from __future__ import annotations

import json
import os
import subprocess
import sys
from getpass import getpass
from pathlib import Path
from typing import Any

from labs.shared.config import ROOT_DIR


def project_root() -> Path:
    return ROOT_DIR


def setup_colab_workspace(root: Path | None = None) -> Path:
    root = root or project_root()
    root = root.resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    return root


def require_google_api_key(env_var: str = "GOOGLE_API_KEY") -> str:
    value = os.getenv(env_var, "").strip()
    if value:
        return value

    value = getpass(f"Enter {env_var}: ").strip()
    if not value:
        raise RuntimeError(f"{env_var} is required for live Gemini calls.")
    os.environ[env_var] = value
    return value


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def show_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def list_artifacts(lab_id: str, root: Path | None = None) -> list[str]:
    root = root or project_root()
    lab_dir = root / "artifacts" / lab_id
    if not lab_dir.exists():
        return []
    return sorted(path.relative_to(root).as_posix() for path in lab_dir.rglob("*") if path.is_file())


def run_lab_tests(lab_id: str, root: Path | None = None) -> subprocess.CompletedProcess[str]:
    root = root or project_root()
    return subprocess.run(
        [sys.executable, "-m", "pytest", f"labs/{lab_id}/tests", "-q"],
        cwd=root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
