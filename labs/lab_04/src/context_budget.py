"""Course-owned context safety bound shared by Lab 4 and later workflows.

Normal Lab 4 fixtures fit without any truncation. When real uploaded/fetched
materials exceed the model-input limit, this module preserves the prompt and
Skill, then records which evidence was kept, shortened, or omitted. It exists
for a real overflow condition; students do not manufacture one for this Lab.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from labs.lab_04.src.retrieval import SourceRecord


# Provider usage reports are the accurate source; this character estimate is the
# standard offline fallback and keeps the lab runnable without a model call.
CHARS_PER_TOKEN = 4

# A snippet shorter than this carries no usable evidence, so a source that
# cannot be given at least this much room is dropped instead of truncated.
MIN_USEFUL_SNIPPET_TOKENS = 10


class SelectedContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[SourceRecord]
    dropped_source_ids: list[str]
    truncated_source_ids: list[str]
    budget_tokens: int
    protected_tokens: int
    estimated_tokens: int


def estimate_tokens(text: str) -> int:
    """Offline token estimate: roughly four characters per token."""
    return len(text) // CHARS_PER_TOKEN


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Cut text down to an estimated token count, on a whole-token boundary."""
    return text[: max(0, max_tokens) * CHARS_PER_TOKEN]


def render_evidence_source(source: SourceRecord) -> str:
    """Render one source exactly as the claim-generation prompt sees it."""
    return f"- source_id={source.source_id}: {source.snippet}\n"


def render_evidence_sources(sources: list[SourceRecord]) -> str:
    """Render the evidence block whose cost competes for context space."""
    return "".join(render_evidence_source(source) for source in sources)


def select_context(
    sources: list[SourceRecord],
    budget_tokens: int,
    protected_tokens: int = 0,
    input_token_counter: Callable[[list[SourceRecord]], int] | None = None,
) -> SelectedContext:
    """Fit retrieved sources into a token budget and report what was lost.

    Sources arrive in Lab 3 relevance order; keep that order.
    """
    if protected_tokens > budget_tokens:
        raise ValueError("protected_tokens cannot exceed budget_tokens")
    if input_token_counter is not None and input_token_counter([]) != protected_tokens:
        raise ValueError("input_token_counter([]) must equal protected_tokens")

    def total_tokens(candidate_sources: list[SourceRecord]) -> int:
        if input_token_counter is not None:
            return input_token_counter(candidate_sources)
        return protected_tokens + estimate_tokens(render_evidence_sources(candidate_sources))

    available = budget_tokens - protected_tokens
    selected: list[SourceRecord] = []
    dropped: list[str] = []
    truncated: list[str] = []

    for source in sources:
        if total_tokens([*selected, source]) <= budget_tokens:
            selected.append(source)
            continue

        if input_token_counter is not None:
            low = 0
            high = len(source.snippet)
            while low < high:
                midpoint = (low + high + 1) // 2
                shortened = source.model_copy(update={"snippet": source.snippet[:midpoint]})
                if total_tokens([*selected, shortened]) <= budget_tokens:
                    low = midpoint
                else:
                    high = midpoint - 1

            snippet = source.snippet[:low]
            if estimate_tokens(snippet) >= MIN_USEFUL_SNIPPET_TOKENS:
                selected.append(source.model_copy(update={"snippet": snippet}))
                truncated.append(source.source_id)
                continue

            dropped.append(source.source_id)
            continue

        used = estimate_tokens(render_evidence_sources(selected))
        blank = source.model_copy(update={"snippet": ""})
        overhead = estimate_tokens(render_evidence_source(blank))
        room = available - used - overhead

        if room >= MIN_USEFUL_SNIPPET_TOKENS:
            snippet = truncate_to_tokens(source.snippet, room)
            while snippet and estimate_tokens(
                render_evidence_sources([*selected, source.model_copy(update={"snippet": snippet})])
            ) > available:
                snippet = snippet[:-1]

            if estimate_tokens(snippet) >= MIN_USEFUL_SNIPPET_TOKENS:
                selected.append(source.model_copy(update={"snippet": snippet}))
                truncated.append(source.source_id)
                continue

        dropped.append(source.source_id)

    return SelectedContext(
        sources=selected,
        dropped_source_ids=dropped,
        truncated_source_ids=truncated,
        budget_tokens=budget_tokens,
        protected_tokens=protected_tokens,
        estimated_tokens=total_tokens(selected),
    )
