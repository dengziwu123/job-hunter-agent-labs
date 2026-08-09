from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from labs.lab_04.src.retrieval import SourceRecord


SupportStatus = Literal["supported", "unsupported"]

# Provided so you can spend the lab on the verification rule, not on tuning a
# word list. `factual_tokens()` below defines the course-owned tokenizer.
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "candidate",
    "experience",
    "has",
    "have",
    "in",
    "of",
    "professional",
    "the",
    "to",
    "with",
}
NEGATION_TOKENS = {"lack", "lacks", "never", "no", "not", "without"}
REQUIREMENT_TOKENS = {"require", "required", "requirement", "requirements", "requires"}
CAPITALIZED_FRAME_WORDS = {"a", "an", "candidate", "the"}
PREDICATE_NORMALIZATION = {
    "build": "build",
    "building": "build",
    "builds": "build",
    "built": "build",
    "develop": "build",
    "developed": "build",
    "developing": "build",
    "develops": "build",
}


class ClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    source_id: str | None = None


class EvidenceNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    source_id: str | None
    supporting_snippet: str | None
    status: SupportStatus


def factual_tokens(text: str) -> set[str]:
    return {
        token.lower().strip(".-")
        for token in re.findall(r"\b[A-Za-z0-9][A-Za-z0-9+#.-]*", text)
    }


def capitalized_factual_tokens(text: str) -> set[str]:
    tokens = {
        token.lower().strip(".-")
        for token in re.findall(r"\b[A-Z][A-Za-z0-9+#.-]*", text)
    }
    first = re.search(r"\b[A-Za-z0-9][A-Za-z0-9+#.-]*", text)
    if first is not None:
        tokens.discard(first.group().lower().strip(".-"))
    return tokens - CAPITALIZED_FRAME_WORDS


def normalized_tokens(text: str) -> set[str]:
    canonical = re.sub(r"n['’]t\b", " not", text.lower())
    canonical = re.sub(r"\bcannot\b", "can not", canonical)
    return {
        PREDICATE_NORMALIZATION.get(token, token)
        for token in factual_tokens(canonical)
    }


def claim_is_supported_by_snippet(claim: str, snippet: str) -> bool:
    """Course-owned deterministic check for obvious misattribution."""
    claim_tokens = normalized_tokens(claim)
    snippet_tokens = normalized_tokens(snippet)
    if bool(claim_tokens & NEGATION_TOKENS) != bool(snippet_tokens & NEGATION_TOKENS):
        return False
    if (
        "candidate" in claim_tokens
        and "candidate" not in snippet_tokens
        and snippet_tokens & REQUIREMENT_TOKENS
    ):
        return False
    claim_numbers = {token for token in claim_tokens if token.isdigit()}
    snippet_numbers = {token for token in snippet_tokens if token.isdigit()}
    if not claim_numbers <= snippet_numbers:
        return False
    if not capitalized_factual_tokens(claim) <= factual_tokens(snippet):
        return False
    key_tokens = claim_tokens - STOP_WORDS
    if not key_tokens:
        return False
    return key_tokens <= snippet_tokens


def build_evidence_notes(claims: list[ClaimInput], sources: list[SourceRecord]) -> list[EvidenceNote]:
    """Verify model-proposed claims against the exact supplied snippets."""
    sources_by_id = {source.source_id: source for source in sources}
    notes: list[EvidenceNote] = []

    for claim in claims:
        source = sources_by_id.get(claim.source_id or "")
        if source is None:
            notes.append(
                EvidenceNote(
                    claim=claim.claim,
                    source_id=None,
                    supporting_snippet=None,
                    status="unsupported",
                )
            )
            continue

        if not claim_is_supported_by_snippet(claim.claim, source.snippet):
            notes.append(
                EvidenceNote(
                    claim=claim.claim,
                    source_id=source.source_id,
                    supporting_snippet=None,
                    status="unsupported",
                )
            )
            continue

        notes.append(
            EvidenceNote(
                claim=claim.claim,
                source_id=source.source_id,
                supporting_snippet=source.snippet,
                status="supported",
            )
        )

    return notes
