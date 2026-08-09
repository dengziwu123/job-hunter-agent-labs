from __future__ import annotations

import argparse
from pathlib import Path

from labs.shared.artifacts import artifact_path, read_json, write_json
from labs.lab_04.src.claim_generation import (
    ClaimGenerationModel,
    claim_provider_input_tokens,
    claim_prompt_protected_tokens,
)
from labs.lab_04.src.context_budget import select_context
from labs.lab_04.src.evidence import ClaimInput, build_evidence_notes
from labs.lab_04.src.retrieval import retrieve_sources
from labs.lab_04.src.prompt_loader import load_task_prompt
from labs.lab_04.src.skill_loader import load_skill, skill_prompt_block


# Generous enough that the three local fixture snippets all fit. Lower it on the
# command line to watch the budget start making decisions.
DEFAULT_CONTEXT_BUDGET_TOKENS = 4_000


def load_claims(path: Path) -> list[ClaimInput]:
    return [ClaimInput.model_validate(item) for item in read_json(path)]


def run(
    use_skill: bool,
    model: ClaimGenerationModel | None = None,
    context_budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
) -> dict:
    model = model or ClaimGenerationModel()
    skill = load_skill(use_skill)
    skill_prompt = skill_prompt_block(skill)
    task_prompt = load_task_prompt()

    sources = retrieve_sources("Python LLM API evidence", limit=3)
    # Fixed prompt instructions and Skill rules are reserved first: a tight
    # budget drops evidence, never the instructions that govern its use.
    settings = getattr(model, "settings", None)
    protected_tokens = claim_prompt_protected_tokens(
        skill_prompt,
        task_prompt=task_prompt,
        settings=settings,
    )
    selected = select_context(
        sources,
        budget_tokens=context_budget_tokens,
        protected_tokens=protected_tokens,
        input_token_counter=lambda candidate_sources: claim_provider_input_tokens(
            candidate_sources,
            skill_prompt,
            task_prompt=task_prompt,
            settings=settings,
        ),
    )
    claims = model.generate_claims(
        selected.sources,
        skill_prompt=skill_prompt,
        task_prompt=task_prompt,
    )
    notes = build_evidence_notes(claims, selected.sources)

    payload = {
        "report": {
            "title": "Job prep evidence report",
            "claim_count": len(notes),
        },
        "claims": [note.model_dump() for note in notes],
        "source_references": [source.model_dump() for source in selected.sources],
        "context_budget": selected.model_dump(exclude={"sources"}),
        "skill": skill.model_dump(),
        "task_prompt": task_prompt.model_dump(exclude={"template"}),
        "model": {
            "mode": model.mode,
            "claim_source": model.claim_source,
            "model_calls": model.calls,
            "model_io": model.last_io,
        },
    }
    write_json(artifact_path("lab_04", "evidence_report.json"), payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--use-skill", action="store_true")
    parser.add_argument("--no-skill", action="store_true")
    parser.add_argument(
        "--context-budget",
        type=int,
        default=DEFAULT_CONTEXT_BUDGET_TOKENS,
        help="Token budget for skill rules plus evidence snippets.",
    )
    args = parser.parse_args()

    payload = run(
        use_skill=args.use_skill and not args.no_skill,
        context_budget_tokens=args.context_budget,
    )
    supported = sum(1 for claim in payload["claims"] if claim["status"] == "supported")
    unsupported = sum(1 for claim in payload["claims"] if claim["status"] == "unsupported")
    budget = payload["context_budget"]

    print(f"mode={payload['model']['mode']}")
    print(f"claim_source={payload['model']['claim_source']}")
    print(f"skill_rules={payload['skill']['rule_count']}")
    print(f"context_budget_tokens={budget['budget_tokens']}")
    print(f"context_protected_tokens={budget['protected_tokens']}")
    print(f"context_used_tokens={budget['estimated_tokens']}")
    print(f"dropped_sources={len(budget['dropped_source_ids'])}")
    print(f"truncated_sources={len(budget['truncated_source_ids'])}")
    print(f"supported_claims={supported}")
    print(f"unsupported_claims={unsupported}")
    print("artifact=artifacts/lab_04/evidence_report.json")


if __name__ == "__main__":
    main()
