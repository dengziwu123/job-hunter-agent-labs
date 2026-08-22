from __future__ import annotations

from time import perf_counter
from typing import Any

from labs.lab_03.src.policies import DraftAction
from labs.lab_05.evals.response_contract import (
    expected_response_kind,
    expected_response_status,
)
from labs.shared.web.contracts import HarnessEvent


POLICY_RESPONSE_STATUS = {
    "draft_created": "ok",
    "needs_approval": "needs_approval",
    # TODO(lab_05): preserve the policy decision at the response boundary.
    "blocked": "ok",
}


def _response_claim(note: dict[str, Any]) -> str:
    """Keep model-authored claim text readable without exposing verifier labels."""
    return " ".join(str(note["claim"]).split())


def apply_response_boundary(
    action: DraftAction,
    evidence_notes: list[dict[str, Any]],
    events: list[HarnessEvent],
) -> dict[str, str]:
    """Turn the real policy/evidence result into one Job Agent response."""
    started = perf_counter()
    supported_claims = [note for note in evidence_notes if note.get("status") == "supported"]
    unsupported_count = sum(note.get("status") != "supported" for note in evidence_notes)
    if supported_claims and unsupported_count:
        evidence_status = "partial"
    elif supported_claims:
        evidence_status = "supported"
    else:
        evidence_status = "unsupported"
    output_status = POLICY_RESPONSE_STATUS[action.status]
    if action.status != "blocked" and evidence_status == "unsupported":
        output_status = "blocked"
    expected_status = expected_response_status(action.status, evidence_status)
    expected_kind = expected_response_kind(action.status, evidence_status)

    if action.status == "blocked":
        response_kind = "refusal" if output_status == "blocked" else "unsafe_draft"
    elif evidence_status == "unsupported":
        response_kind = "evidence_refusal"
    elif action.status == "needs_approval" and output_status == "needs_approval":
        response_kind = "approval_required"
    elif action.status == "draft_created" and output_status == "ok":
        response_kind = "grounded_draft"
    else:
        response_kind = "unexpected_boundary"

    grounded_response = "\n".join(_response_claim(note) for note in supported_claims)

    if response_kind == "refusal":
        message = (
            "I can't add experience that is not supported by your profile. "
            "I can instead strengthen a truthful bullet using evidence you provide."
        )
    elif response_kind == "approval_required":
        message = (
            f"{grounded_response}\n\n"
            "I prepared the content locally, but I did not send or submit anything. "
            "An external action needs your explicit approval."
        )
    elif response_kind == "unsafe_draft":
        # This is the visible starter failure: the bad blocked -> ok mapping
        # turns the unsafe request itself into an apparently accepted draft.
        message = f"Draft created: {action.content}"
    elif response_kind == "evidence_refusal":
        message = (
            "I couldn't create a grounded draft from the supplied evidence. "
            "Please provide a source that supports the requested claim."
        )
    elif response_kind == "grounded_draft":
        message = grounded_response
    else:
        message = "I couldn't produce a response that preserves the upstream decisions."

    duration_ms = max(1, int((perf_counter() - started) * 1000))
    events.append(
        HarnessEvent(
            sequence=len(events) + 1,
            type="trace",
            status="blocked" if output_status == "blocked" else "completed",
            component="labs.lab_05.src.response_boundary",
            operation="response_boundary",
            summary=f"Response boundary mapped {action.status} to {output_status}",
            duration_ms=duration_ms,
            details={
                "semantic_key": "response.apply_boundary",
                "input_policy_status": action.status,
                "input_evidence_status": evidence_status,
                "output_status": output_status,
                "expected": expected_status,
                "observed": output_status,
                "response_kind": response_kind,
                "expected_response_kind": expected_kind,
                "supported_claim_count": len(supported_claims),
                "unsupported_claim_count": unsupported_count,
                "external_action_performed": False,
            },
        )
    )
    return {"status": output_status, "message": message, "response_kind": response_kind}
