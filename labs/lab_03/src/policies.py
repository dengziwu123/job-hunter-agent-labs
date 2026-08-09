from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


ActionStatus = Literal["draft_created", "needs_approval", "blocked"]


class DraftAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: str
    content: str
    status: ActionStatus
    reason: str


SAFE_DRAFT_ACTIONS = {"outreach_draft", "resume_bullet", "prep_plan"}
APPROVAL_REQUIRED_ACTIONS = {"send_email", "apply_job", "publish_profile", "update_external_system"}


def classify_action(action_type: str, content: str) -> DraftAction:
    """Classify an action without performing any external side effect."""
    lowered_content = content.lower()
    unsupported_claim_markers = (
        "even though it is not",
        "not in my profile",
        "not in my resume",
        "fake experience",
        "fabricat",
        "invented experience",
        "unsupported claim",
        "without evidence",
    )

    if any(marker in lowered_content for marker in unsupported_claim_markers):
        return DraftAction(
            action_type=action_type,
            content=content,
            status="blocked",
            reason="The action contains an unsupported or fabricated experience claim.",
        )

    if action_type in SAFE_DRAFT_ACTIONS:
        return DraftAction(
            action_type=action_type,
            content=content,
            status="draft_created",
            reason="This action is limited to a local draft and does not affect external systems.",
        )

    if action_type in APPROVAL_REQUIRED_ACTIONS:
        return DraftAction(
            action_type=action_type,
            content=content,
            status="needs_approval",
            reason="This action could affect an external system and requires approval.",
        )

    return DraftAction(
        action_type=action_type,
        content=content,
        status="needs_approval",
        reason="Unknown actions are held for approval and are never executed automatically.",
    )


def draft_action(action_type: str, content: str) -> DraftAction:
    return classify_action(action_type, content)
