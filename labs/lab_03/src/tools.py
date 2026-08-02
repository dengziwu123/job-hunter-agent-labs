from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from labs.shared.artifacts import read_json
from labs.shared.config import ROOT_DIR


class SourceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    title: str
    path: str
    snippet: str


def search_sources(
    query: str,
    limit: int = 3,
    source_items: list[dict] | None = None,
) -> list[SourceResult]:
    """Search local course sources for job-relevant evidence."""
    # TODO(lab_03): map source_items (or local fixtures when it is None) into
    # SourceResult objects, rank them for query, and return at most limit.
    # Keep source_id stable; Lab 4 evidence relies on it.
    raise NotImplementedError("TODO: implement local source search.")


def load_source_fixture(path: Path | None = None) -> list[dict]:
    fixture_path = path or ROOT_DIR / "labs" / "lab_03" / "data" / "sources.json"
    data = read_json(fixture_path)
    if not isinstance(data, list):
        raise ValueError("sources.json must contain a list.")
    return data
