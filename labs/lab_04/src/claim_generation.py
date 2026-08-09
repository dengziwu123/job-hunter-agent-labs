from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from labs.shared.artifacts import read_json
from labs.shared.config import ROOT_DIR, Settings
from labs.shared.llm import LlmSession
from labs.shared.providers import provider_structured_request
from labs.lab_04.src.context_budget import CHARS_PER_TOKEN, render_evidence_sources
from labs.lab_04.src.evidence import ClaimInput
from labs.lab_04.src.prompt_loader import LoadedTaskPrompt, load_task_prompt, render_task_prompt
from labs.lab_04.src.retrieval import SourceRecord


FIXTURE_CLAIMS_PATH = ROOT_DIR / "labs" / "lab_04" / "data" / "claims.json"

# SDK/OpenAPI schema form (google.genai types.Schema), which is what the
# response_schema config field documents — not standard JSON Schema.
CLAIMS_RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "claim": {"type": "STRING"},
            "source_id": {
                "type": "STRING",
                "description": "The source_id that supports this claim, or an empty string if none does.",
            },
        },
        "required": ["claim", "source_id"],
    },
}


DEFAULT_BUDGET_SETTINGS = Settings(model="gemini-3.1-flash-lite")


def render_claim_provider_input(
    prompt: str,
    settings: Settings | None = None,
) -> str:
    """Render the exact structured request body sent to the provider."""
    return json.dumps(
        provider_structured_request(
            settings or DEFAULT_BUDGET_SETTINGS,
            prompt,
            CLAIMS_RESPONSE_SCHEMA,
        ),
        indent=2,
        ensure_ascii=False,
    )


def render_request_context(request_context: str = "") -> str:
    """Render Lab 3's bounded conversation context, or nothing for the CLI."""
    return request_context.strip()


def render_claim_generation_prompt(
    sources: list[SourceRecord],
    skill_prompt: str = "",
    request_context: str = "",
    task_prompt: LoadedTaskPrompt | None = None,
    available_tools: list[dict] | None = None,
    job_openings: list[dict] | None = None,
) -> str:
    """Render the student's exact task prompt with course-supplied values."""
    return render_task_prompt(
        task_prompt or load_task_prompt(),
        skill_rules=skill_prompt,
        user_request=render_request_context(request_context),
        evidence_sources=render_evidence_sources(sources),
        available_tools=available_tools,
        job_openings=job_openings,
    )


def claim_prompt_protected_tokens(
    skill_prompt: str = "",
    request_context: str = "",
    task_prompt: LoadedTaskPrompt | None = None,
    available_tools: list[dict] | None = None,
    job_openings: list[dict] | None = None,
    *,
    settings: Settings | None = None,
) -> int:
    """Reserve task instructions, Skill rules and the request before evidence.

    Everything the model is going to be told regardless of which sources fit
    belongs in the reservation, or the budget under-counts the real prompt.

    The context selector estimates the evidence block separately. Rounding the
    scaffold up prevents those two estimates from undercounting the combined
    prompt by one token at their boundary.
    """
    scaffold = render_claim_generation_prompt(
        [],
        skill_prompt,
        request_context,
        task_prompt,
        available_tools,
        job_openings,
    )
    return (len(render_claim_provider_input(scaffold, settings)) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def claim_provider_input_tokens(
    sources: list[SourceRecord],
    skill_prompt: str = "",
    request_context: str = "",
    task_prompt: LoadedTaskPrompt | None = None,
    available_tools: list[dict] | None = None,
    job_openings: list[dict] | None = None,
    *,
    settings: Settings | None = None,
) -> int:
    """Measure the complete provider request after rendering its evidence."""
    prompt = render_claim_generation_prompt(
        sources,
        skill_prompt,
        request_context,
        task_prompt,
        available_tools,
        job_openings,
    )
    provider_input = render_claim_provider_input(prompt, settings)
    return (len(provider_input) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def load_fixture_claims(path: Path | None = None) -> list[ClaimInput]:
    return [ClaimInput.model_validate(item) for item in read_json(path or FIXTURE_CLAIMS_PATH)]


def fallback_claims(
    sources: list[SourceRecord],
    request_context: str = "",
) -> list[ClaimInput]:
    if not sources:
        return load_fixture_claims()
    claims = [ClaimInput(claim=source.snippet, source_id=source.source_id) for source in sources[:2]]
    lowered = request_context.lower()
    if "kubernetes" in lowered:
        unsupported = "The candidate has production Kubernetes experience."
    elif any(term in lowered for term in ("current opening", "openings", "在招", "职位", "岗位")):
        unsupported = "The candidate meets every requirement of the returned openings."
    else:
        unsupported = "The candidate led a production multi-agent migration."
    claims.append(ClaimInput(claim=unsupported, source_id=None))
    return claims


class ClaimGenerationModel:
    """Course-provided plumbing: Gemini proposes the report claims for Lab 4.

    Live mode: the model reads the retrieved snippets and proposes claims,
    naming the source_id it believes supports each one. The model may still
    overreach — catching that is the evidence verifier's job, not the prompt's.
    Offline mode (or a malformed model response): falls back to the
    deterministic fixture claims so the demo and CI stay runnable.

    `claim_source` records where the claims actually came from:
    "model" or "fixture".
    """

    def __init__(self, settings: Settings | None = None):
        self._session = LlmSession(settings)
        self.claim_source: str | None = None
        self.last_io: dict = {}

    @property
    def live(self) -> bool:
        return self._session.live

    @property
    def mode(self) -> str:
        return self._session.mode

    @property
    def calls(self) -> int:
        return self._session.calls

    @property
    def settings(self) -> Settings:
        return self._session.settings

    def generate_claims(
        self,
        sources: list[SourceRecord],
        skill_prompt: str = "",
        request_context: str = "",
        task_prompt: LoadedTaskPrompt | None = None,
        available_tools: list[dict] | None = None,
        job_openings: list[dict] | None = None,
    ) -> list[ClaimInput]:
        """Propose report claims from the snippets that fit in the context.

        `skill_prompt` carries the loaded Skill rules. It is protected content:
        it goes in front of the evidence and is never trimmed to make room for
        sources. Passing an empty string is supported by the CLI, but the Lab's
        main comparison is Lab 3 Before versus Lab 4 After.

        `request_context` is Lab 3's bounded conversation context: what the
        candidate actually asked for, this turn and the ones the state kept.
        Also protected — the report should answer the question that was asked,
        not a generic one.
        """
        prompt = render_claim_generation_prompt(
            sources,
            skill_prompt,
            request_context,
            task_prompt,
            available_tools,
            job_openings,
        )
        if not self.live:
            self.claim_source = "fixture"
            claims = fallback_claims(sources, request_context)
            self._record_io(prompt, None, claims, "offline_fixture")
            return claims

        payload = None
        try:
            payload = self._session.complete_json(prompt, CLAIMS_RESPONSE_SCHEMA, offline_payload=[])
            claims = []
            for item in payload:
                if not isinstance(item, dict):
                    raise TypeError("claim item is not an object")
                if not item["claim"]:
                    raise KeyError("claim text is empty")
                # Both keys are required by the schema; a missing key is a
                # schema violation and falls back via KeyError. An *empty*
                # source_id is in-protocol: it marks an intentionally
                # unsupported claim and becomes None.
                claims.append(ClaimInput(claim=item["claim"], source_id=item["source_id"] or None))
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError):
            claims = []

        if not claims:
            self.claim_source = "fixture"
            claims = fallback_claims(sources, request_context)
            self._record_io(prompt, payload, claims, "invalid_model_fallback")
            return claims

        self.claim_source = "model"
        self._record_io(prompt, payload, claims, "model")
        return claims

    def _record_io(
        self,
        prompt: str,
        raw_payload: object,
        claims: list[ClaimInput],
        source: str,
    ) -> None:
        self.last_io = {
            "provider": self._session.settings.provider,
            "model": self._session.settings.model,
            "actual_provider_input": render_claim_provider_input(prompt, self.settings),
            "raw_model_output": (
                None
                if source == "offline_fixture"
                else json.dumps(raw_payload, indent=2, ensure_ascii=False)
            ),
            "validated_output": json.dumps(
                [claim.model_dump() for claim in claims],
                indent=2,
                ensure_ascii=False,
            ),
            "output_source": source,
        }
