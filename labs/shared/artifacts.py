from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

from labs.shared.config import ROOT_DIR


_active_artifact_root: ContextVar[Path | None] = ContextVar("active_artifact_root", default=None)


def artifact_root() -> Path:
    """Return the current run's artifact root, or the normal workspace root."""
    return _active_artifact_root.get() or (ROOT_DIR / "artifacts")


@contextmanager
def artifact_scope(root: Path) -> Iterator[None]:
    """Isolate artifacts written by a comparison side from normal workspace state."""
    root = root.resolve()
    normal_root = (ROOT_DIR / "artifacts").resolve()
    if root != normal_root and normal_root not in root.parents:
        raise ValueError("Artifact scope must stay inside artifacts/.")
    root.mkdir(parents=True, exist_ok=True)
    token = _active_artifact_root.set(root)
    try:
        yield
    finally:
        _active_artifact_root.reset(token)


def artifact_path(*parts: str) -> Path:
    path = artifact_root() / Path(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def persistent_artifact_path(*parts: str) -> Path:
    """Return an artifacts/ path that is not redirected by a run scope."""
    path = ROOT_DIR / "artifacts" / Path(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def task_state_path(stage_id: str, workspace_id: str) -> Path:
    return task_state_directory(stage_id, workspace_id) / "task_state.json"


def task_state_directory(stage_id: str, workspace_id: str) -> Path:
    return persistent_artifact_path("task-state", stage_id, workspace_id)


def task_state_revision_path(stage_id: str, workspace_id: str, revision: int) -> Path:
    return task_state_directory(stage_id, workspace_id) / "revisions" / f"revision_{revision}.json"


def relative_artifact_path(path: Path) -> str:
    return path.resolve().relative_to((ROOT_DIR / "artifacts").resolve()).as_posix()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
