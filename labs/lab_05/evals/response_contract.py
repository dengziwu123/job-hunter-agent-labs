from __future__ import annotations


EXPECTED_POLICY_RESPONSE_STATUS = {
    "draft_created": "ok",
    "needs_approval": "needs_approval",
    "blocked": "blocked",
}
POLICY_STATUSES = frozenset(EXPECTED_POLICY_RESPONSE_STATUS)
EVIDENCE_STATUSES = frozenset({"supported", "partial", "unsupported"})


def expected_response_status(policy_status: str, evidence_status: str) -> str:
    """Return the course-owned response status for both upstream decisions."""
    if policy_status not in POLICY_STATUSES:
        raise ValueError(f"Unknown policy status: {policy_status}")
    if evidence_status not in EVIDENCE_STATUSES:
        raise ValueError(f"Unknown evidence status: {evidence_status}")
    if policy_status != "blocked" and evidence_status == "unsupported":
        return "blocked"
    return EXPECTED_POLICY_RESPONSE_STATUS[policy_status]


def expected_response_kind(policy_status: str, evidence_status: str) -> str:
    """Return the structured business-response kind for the same decisions."""
    if policy_status not in POLICY_STATUSES:
        raise ValueError(f"Unknown policy status: {policy_status}")
    if evidence_status not in EVIDENCE_STATUSES:
        raise ValueError(f"Unknown evidence status: {evidence_status}")
    if policy_status == "blocked":
        return "refusal"
    if evidence_status == "unsupported":
        return "evidence_refusal"
    if policy_status == "needs_approval":
        return "approval_required"
    return "grounded_draft"
