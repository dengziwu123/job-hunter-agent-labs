from __future__ import annotations

from typing import Any

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
    # TODO(lab_03): create revision 1 when previous is None. Otherwise validate
    # the previous state, keep its state_id, increment revision, append a bounded
    # copy of only the current user request, retain at most
    # MAX_STATE_USER_REQUESTS, and replace the report/tool progress with this
    # run's validated values.
    raise NotImplementedError("TODO(lab_03): advance the persisted task state.")
