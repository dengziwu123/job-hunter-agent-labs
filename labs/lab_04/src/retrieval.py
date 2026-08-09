from __future__ import annotations

from pathlib import Path

from labs.lab_03.src.tools import SourceResult, load_source_fixture, search_sources


# Lab 4 deliberately keeps the Lab 3 source contract instead of defining a
# second retrieval record. The alias preserves the teaching name used below.
SourceRecord = SourceResult


def load_sources(path: Path | None = None) -> list[SourceRecord]:
    return [SourceRecord.model_validate(item) for item in load_source_fixture(path)]


def retrieve_sources(
    query: str,
    limit: int = 3,
    source_items: list[dict] | None = None,
) -> list[SourceRecord]:
    """Reuse the Lab 3 tool; students do not build a second retrieval system."""
    return search_sources(query, limit=limit, source_items=source_items)
