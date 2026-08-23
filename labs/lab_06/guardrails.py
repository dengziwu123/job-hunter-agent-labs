from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from labs.lab_03.src.policies import draft_action


ApprovalStatus = Literal["allowed_draft", "needs_approval", "blocked"]


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ApprovalStatus
    reason: str


def require_approval(action_type: str, content: str, unsupported_claim: bool = False) -> ApprovalDecision:
    lowered_content = content.lower()
    fabricated_claim = "invent" in lowered_content or (
        "unsupported" in lowered_content and "claim" in lowered_content
    )
    if unsupported_claim or fabricated_claim:
        return ApprovalDecision(
            status="blocked",
            reason="The action uses an unsupported or fabricated claim and cannot proceed.",
        )

    action = draft_action(action_type, content)
    status: ApprovalStatus = (
        "allowed_draft" if action.status == "draft_created" else action.status
    )
    return ApprovalDecision(status=status, reason=action.reason)
