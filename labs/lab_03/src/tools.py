from __future__ import annotations

from pathlib import Path
import re

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
    """Search current local sources and return stable, query-ranked results.

    ``None`` means that the CLI caller did not provide workspace materials, so
    the course fixture is used.  An explicitly supplied empty list stays empty
    so a workspace cannot accidentally receive unrelated fixture evidence.
    """
    if limit <= 0:
        return []

    items = load_source_fixture() if source_items is None else source_items
    query_terms = _search_terms(query)
    ranked: list[tuple[int, int, SourceResult]] = []

    for position, item in enumerate(items):
        result = SourceResult.model_validate(item)
        searchable = _search_terms(f"{result.title} {result.snippet}")
        score = sum(searchable.count(term) for term in query_terms)
        if query_terms and score == 0:
            continue
        if query_terms and " ".join(query_terms) in " ".join(searchable):
            score += len(query_terms)
        ranked.append((score, position, result))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [result for _, _, result in ranked[:limit]]


def _search_terms(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.lower())


def load_source_fixture(path: Path | None = None) -> list[dict]:
    fixture_path = path or ROOT_DIR / "labs" / "lab_03" / "data" / "sources.json"
    data = read_json(fixture_path)
    if not isinstance(data, list):
        raise ValueError("sources.json must contain a list.")
    return data
