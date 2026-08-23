from __future__ import annotations

from labs.lab_03.src.policies import draft_action
from labs.lab_04.src.claim_generation import render_request_context
from labs.lab_04.src.context_budget import CHARS_PER_TOKEN
from labs.lab_06 import model
from labs.lab_06.contracts import AgentContract, AgentInstruction


INSTRUCTION = AgentInstruction(
    role="Create the requested job-prep draft from the bounded summarize handoff.",
    objective="Generate the requested draft format and pass it to the existing action policy.",
    boundary="Create local drafts only; never execute external actions or add unsupported claims.",
)

CONTRACT = AgentContract(
    input_fields=[
        "fit_gap_summary",
        "prep_plan",
        "requested_action",
        "user_request",
        "skill_prompt",
    ],
    output_fields=["action_type", "status", "content", "reason"],
    failure_statuses=["invalid_input", "model_failed", "blocked", "needs_approval"],
    trace_events=["model_call", "action_draft", "approval_decision"],
)


ACTION_PROMPT_DIRECTIONS = {
    "outreach_draft": (
        "Draft a short factual outreach message from this fit summary and prep plan. "
        "Do not send it.\n"
    ),
    "resume_bullet": (
        "Draft one concise factual resume bullet from supported fit evidence. "
        "Do not invent experience or metrics.\n"
    ),
    "prep_plan": (
        "Create a short interview preparation plan from the fit gaps and next steps. "
        "Keep it local.\n"
    ),
}


def action_kind(requested_action: str) -> str:
    if requested_action in {"resume_bullet", "prep_plan"}:
        return requested_action
    return "outreach_draft"


def render_action_prompt(input_data: dict) -> str:
    kind = action_kind(input_data["requested_action"])
    return (
        f"{input_data['skill_prompt']}"
        f"{render_request_context(input_data['user_request'])}"
        f"Requested draft type: {kind}\n"
        f"{ACTION_PROMPT_DIRECTIONS[kind]}"
        f"Fit summary: {input_data['fit_gap_summary']}\n"
        f"Prep plan: {input_data['prep_plan']}"
    )


def offline_action_text(input_data: dict) -> str:
    kind = action_kind(input_data["requested_action"])
    if kind == "resume_bullet":
        return f"Resume bullet draft: {input_data['fit_gap_summary']}"
    if kind == "prep_plan":
        days = input_data.get("prep_plan", {}).get("days", [])
        tasks = [str(day.get("task", "")).strip() for day in days]
        return "Interview prep plan: " + "; ".join(task for task in tasks if task)
    return f"Outreach draft based on supported facts: {input_data['fit_gap_summary']}"


def action_model_operation(input_data: dict) -> tuple[str, str]:
    kind = action_kind(input_data["requested_action"])
    if kind == "outreach_draft":
        return "draft_outreach", "model.draft_outreach"
    return f"draft_{kind}", f"model.draft_{kind}"


def estimate_action_prompt_tokens(input_data: dict) -> int:
    complete_prompt = (
        f"{model.render_system_prompt(INSTRUCTION)}\n"
        f"{render_action_prompt(input_data)}"
    )
    return (
        len(complete_prompt) + CHARS_PER_TOKEN - 1
    ) // CHARS_PER_TOKEN


def measure_action_prompt(input_data: dict, budget_tokens: int) -> dict[str, int]:
    estimated_tokens = estimate_action_prompt_tokens(input_data)
    if estimated_tokens > budget_tokens:
        raise ValueError(
            f"action prompt ({estimated_tokens} tokens) exceeds context budget "
            f"({budget_tokens} tokens)"
        )
    return {
        "budget_tokens": budget_tokens,
        "estimated_tokens": estimated_tokens,
        "remaining_tokens": budget_tokens - estimated_tokens,
    }


def run(input_data: dict) -> dict:
    # Use labs.lab_06.model.traced_complete() with render_action_prompt(input_data)
    # and offline_action_text(input_data). The renderer and fallback must match
    # requested_action; measure_action_prompt() protects the user prompt and the
    # role system prompt before allowing this model call.
    # Draft only: never perform the external action here.
    # Classify the generated content with the existing Lab 3 action policy.
    operation, semantic_key = action_model_operation(input_data)
    content = model.traced_complete(
        agent="action",
        operation=operation,
        semantic_key=semantic_key,
        prompt=render_action_prompt(input_data),
        offline_text=offline_action_text(input_data),
        instruction=INSTRUCTION,
    ).strip()
    return draft_action(input_data["requested_action"], content).model_dump()
