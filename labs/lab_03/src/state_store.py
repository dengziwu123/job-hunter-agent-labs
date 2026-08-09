from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from labs.lab_02.src.schemas import FitGapReport


MAX_STATE_USER_REQUESTS = 20
MAX_STATE_USER_REQUEST_CHARACTERS = 4_000


class ManagedTaskState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state_id: str
    revision: int
    user_requests: list[str]
    report: FitGapReport
    source_ids: list[str]
    action_status: str
    latest_run_id: str
    validation_status: str = "valid"


def advance_task_state(
    previous: dict[str, Any] | None,
    *,
    user_request: str,
    report: dict[str, Any],
    source_ids: list[str],
    action_status: str,
    run_id: str,
) -> ManagedTaskState:
    validated_report = FitGapReport.model_validate(report)
    bounded_request = user_request[:MAX_STATE_USER_REQUEST_CHARACTERS]

    if previous is None:
        state_id = f"state_{uuid4().hex}"
        revision = 1
        user_requests = [bounded_request]
    else:
        previous_state = ManagedTaskState.model_validate(previous)
        state_id = previous_state.state_id
        revision = previous_state.revision + 1
        user_requests = [*previous_state.user_requests, bounded_request][
            -MAX_STATE_USER_REQUESTS:
        ]

    return ManagedTaskState(
        state_id=state_id,
        revision=revision,
        user_requests=user_requests,
        report=validated_report,
        source_ids=list(source_ids),
        action_status=action_status,
        latest_run_id=run_id,
        validation_status="valid",
    )
