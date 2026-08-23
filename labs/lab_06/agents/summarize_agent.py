from __future__ import annotations

from labs.lab_06 import model
from labs.lab_06.contracts import AgentContract, AgentInstruction
from labs.lab_02.src.schemas import FitGapReport
from labs.lab_04.src.claim_generation import render_request_context
from labs.lab_04.src.context_budget import CHARS_PER_TOKEN, render_evidence_sources
from labs.lab_04.src.evidence import ClaimInput, build_evidence_notes
from labs.lab_04.src.retrieval import SourceRecord


INSTRUCTION = AgentInstruction(
    role="Summarize selected job evidence into a structured fit and preparation handoff.",
    objective="Produce a FitGapReport, verified evidence notes, and a practical preparation plan.",
    boundary="Use only supplied snippets and constraints; mark missing support and never guess experience.",
)

CONTRACT = AgentContract(
    input_fields=[
        "sources",
        "prior_report",
        "candidate_constraints",
        "user_request",
        "skill_prompt",
    ],
    output_fields=["fit_gap_report", "evidence_notes", "prep_plan"],
    failure_statuses=["invalid_input", "model_failed", "evidence_failed"],
    trace_events=["model_call", "summary_output"],
)

SUMMARIZE_PROMPT_DIRECTIONS = (
    "Summarize job fit using only these source snippets. "
    "Name one strength and one gap.\n"
)


def render_summarize_prompt(
    sources,
    candidate_constraints: list[str],
    skill_prompt: str,
    user_request: str,
) -> str:
    constraints = "\n".join(f"- {constraint}" for constraint in candidate_constraints)
    return (
        f"{skill_prompt}"
        f"{render_request_context(user_request)}"
        "Candidate constraints:\n"
        f"{constraints}\n\n"
        f"{SUMMARIZE_PROMPT_DIRECTIONS}"
        f"{render_evidence_sources(sources)}"
    )


def summarize_prompt_protected_tokens(
    skill_prompt: str,
    candidate_constraints: list[str],
    user_request: str,
) -> int:
    scaffold = (
        f"{model.render_system_prompt(INSTRUCTION)}\n"
        f"{render_summarize_prompt([], candidate_constraints, skill_prompt, user_request)}"
    )
    return (len(scaffold) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def run(input_data: dict) -> dict:
    # Use labs.lab_06.model.traced_complete(..., instruction=INSTRUCTION) to
    # write the fit summary text from the research snippets. Every call consumes one
    # max_model_calls budget slot; without GOOGLE_API_KEY it returns offline_text.
    # Put input_data["skill_prompt"] before the task prompt so the Lab 4 rules
    # remain active after this capability moves into the multi-agent harness.
    # Build the prompt with render_summarize_prompt() so its fixed instructions,
    # Skill rules, bounded user request, and evidence block match the Lab 4
    # context-budget measurement.
    # Reuse FitGapReport and build_evidence_notes instead of inventing new
    # report/evidence contracts inside the multi-agent layer.
    report = FitGapReport.model_validate(input_data["prior_report"])
    sources = [SourceRecord.model_validate(source) for source in input_data["sources"]]
    fit_summary = model.traced_complete(
        agent="summarize",
        operation="summarize_fit",
        semantic_key="model.summarize_fit",
        prompt=render_summarize_prompt(
            sources,
            input_data["candidate_constraints"],
            input_data["skill_prompt"],
            input_data["user_request"],
        ),
        offline_text=report.fit_summary,
        instruction=INSTRUCTION,
    ).strip()
    updated_report = report.model_copy(update={"fit_summary": fit_summary})
    evidence_notes = build_evidence_notes(
        [ClaimInput(claim=source.snippet, source_id=source.source_id) for source in sources],
        sources,
    )
    prep_plan = {
        "days": [
            {"day": day, "task": task}
            for day, task in enumerate(updated_report.recommended_next_steps, start=1)
        ]
    }
    return {
        "fit_gap_report": updated_report.model_dump(),
        "evidence_notes": [note.model_dump() for note in evidence_notes],
        "prep_plan": prep_plan,
    }
