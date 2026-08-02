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
    # TODO(lab_03): implement the draft-only action boundary.
    #
    # Expected behavior:
    # - SAFE_DRAFT_ACTIONS return status="draft_created"
    # - APPROVAL_REQUIRED_ACTIONS return status="needs_approval"
    # - fake experience or unsupported claims return status="blocked"
    # - unknown external actions should not execute anything
    return DraftAction(
        action_type=action_type,
        content=content,
        status="draft_created",
        reason="TODO: policy not implemented yet.",
    )


def draft_action(action_type: str, content: str) -> DraftAction:
    return classify_action(action_type, content)
