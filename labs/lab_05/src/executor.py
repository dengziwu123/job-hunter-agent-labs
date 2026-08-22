from __future__ import annotations

from collections.abc import Callable
from typing import Any

from labs.lab_03.src.policies import DraftAction, draft_action
from labs.lab_03.web_adapter import requested_action_type
from labs.lab_04.src.evidence import ClaimInput, EvidenceNote, build_evidence_notes
from labs.lab_04.src.retrieval import SourceRecord
from labs.lab_05.src.response_boundary import apply_response_boundary
from labs.shared.web.contracts import HarnessEvent


def execute_job_agent_case(
    user_prompt: str,
    *,
    events: list[HarnessEvent] | None = None,
    fixture: dict[str, Any] | None = None,
    upstream_runner: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute the one Job Agent seam used by Chat and deterministic eval.

    Chat supplies the real Lab 4 payload whose policy/evidence events are
    already in ``events``. Eval supplies frozen model/tool outputs, then this
    callable still runs the student's real Lab 3 policy, Lab 4 evidence rule,
    and Lab 5 response boundary.
    """
    trajectory = events if events is not None else []
    if (fixture is None) == (upstream_runner is None):
        raise ValueError("Provide exactly one of fixture or upstream_runner.")

    if fixture is not None:
        action, notes = _run_fixture_primitives(user_prompt, fixture)
        mode = "fixture"
    else:
        assert upstream_runner is not None
        upstream_payload = upstream_runner()
        action = DraftAction.model_validate(upstream_payload["draft_action"])
        notes = [EvidenceNote.model_validate(note) for note in upstream_payload["claims"]]
        mode = "stage"

    _record_decision_checkpoints(action, notes, trajectory)
    if fixture is not None and fixture.get("tool_result"):
        _record_fixture_tool_result(fixture["tool_result"], trajectory)
    response = apply_response_boundary(
        action,
        [note.model_dump() for note in notes],
        trajectory,
    )
    trajectory.append(
        HarnessEvent(
            sequence=len(trajectory) + 1,
            type="trace",
            status="blocked" if response["status"] == "blocked" else "completed",
            component="labs.lab_05.src.executor",
            operation="final_response",
            summary=f"Returned the Job Agent business response with status {response['status']}",
            details={
                "semantic_key": "response.finalize",
                "output_status": response["status"],
                "response_kind": response["response_kind"],
                "assistant_message": response["message"],
                "external_action_performed": False,
            },
        )
    )
    return {
        **response,
        "mode": mode,
        "draft_action": action.model_dump(),
        "evidence_notes": [note.model_dump() for note in notes],
        "trajectory": [event.model_dump() for event in trajectory],
    }


def _run_fixture_primitives(
    user_prompt: str,
    fixture: dict[str, Any],
) -> tuple[DraftAction, list[EvidenceNote]]:
    draft_content = fixture.get("draft_content") or user_prompt
    policy_content = fixture.get("policy_content")
    if policy_content is None:
        action_type = requested_action_type(user_prompt)
        policy_content = f"{user_prompt}\n\nDraft:\n{draft_content}"
    else:
        action_type = fixture["action_type"]
    action = draft_action(action_type, policy_content)
    claims = [ClaimInput.model_validate(item) for item in fixture.get("claims", [])]
    sources = [SourceRecord.model_validate(item) for item in fixture.get("sources", [])]
    notes = build_evidence_notes(claims, sources)
    return action, notes


def _record_fixture_tool_result(
    tool_result: dict[str, Any],
    events: list[HarnessEvent],
) -> None:
    """Record the frozen external-tool outcome without performing the action."""
    operation = str(tool_result["operation"])
    status = str(tool_result["status"])
    output_status = str(tool_result["output_status"])
    external_action_performed = bool(tool_result["external_action_performed"])
    events.append(
        HarnessEvent(
            sequence=len(events) + 1,
            type="tool_result",
            status=status,
            component="labs.lab_05.evals.fixture_tool",
            operation=operation,
            summary=f"Frozen {operation} tool result returned {output_status}",
            details={
                "semantic_key": "tool.external_action_result",
                "output_status": output_status,
                "external_action_performed": external_action_performed,
            },
        )
    )


def _record_decision_checkpoints(
    action: DraftAction,
    notes: list[EvidenceNote],
    events: list[HarnessEvent],
) -> None:
    """Publish one normalized event contract after either real execution path."""
    events.append(
        HarnessEvent(
            sequence=len(events) + 1,
            type="policy",
            status="blocked" if action.status == "blocked" else "completed",
            component="labs.lab_03.src.policies",
            operation="policy_checkpoint",
            summary=f"Action policy returned {action.status}",
            details={
                "semantic_key": "policy.decision_checkpoint",
                "source_operation": "classify_action",
                "action_type": action.action_type,
                "output_status": action.status,
                "reason": action.reason,
            },
        )
    )

    supported_count = sum(note.status == "supported" for note in notes)
    unsupported_count = sum(note.status == "unsupported" for note in notes)
    if supported_count and unsupported_count:
        evidence_status = "partial"
    elif supported_count:
        evidence_status = "supported"
    else:
        evidence_status = "unsupported"
    events.append(
        HarnessEvent(
            sequence=len(events) + 1,
            type="evidence",
            status="completed",
            component="labs.lab_04.src.evidence",
            operation="evidence_checkpoint",
            summary=f"Evidence check returned {evidence_status}",
            details={
                "semantic_key": "evidence.decision_checkpoint",
                "source_operation": "build_evidence_notes",
                "output_status": evidence_status,
                "supported": supported_count,
                "unsupported": unsupported_count,
            },
        )
    )
