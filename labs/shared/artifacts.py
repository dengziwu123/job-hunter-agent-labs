from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from labs.shared.config import ROOT_DIR


def artifact_path(*parts: str) -> Path:
    path = ROOT_DIR / "artifacts" / Path(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))

