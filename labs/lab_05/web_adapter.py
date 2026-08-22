from __future__ import annotations

import uuid

from labs.shared.artifacts import (
    artifact_path,
    persistent_artifact_path,
    relative_artifact_path,
    write_json,
)
from labs.shared.config import ROOT_DIR
from labs.lab_04.src.mcp_client_adapter import MCPBoundaryError
from labs.lab_04.web_adapter import (
    FAILURE_EVENT_TYPES,
    build_evidence_capability,
    failure_location,
)
from labs.lab_05.src import executor
from labs.shared.web.contracts import ArtifactLink, ChatRequest, ChatResponse, EvalResponse, HarnessEvent
from labs.shared.web.errors import StageExecutionError
from labs.shared.web.materials import MaterialStore
from labs.shared.web.tracing import record_run_trace, write_run_trace


_CANDIDATE_CHECKPOINTS = {
    "policy_checkpoint",
    "evidence_checkpoint",
    "response_boundary",
    "final_response",
}
_CANDIDATE_DETAIL_FIELDS = (
    "semantic_key",
    "input_policy_status",
    "input_evidence_status",
    "output_status",
    "response_kind",
    "external_action_performed",
)


class Lab05Adapter:
    stage_id = "lab_05"

    def __init__(self, materials: MaterialStore) -> None:
        self.materials = materials

    def chat(self, request: ChatRequest) -> ChatResponse:
        run_id = f"run_{uuid.uuid4().hex}"
        events: list[HarnessEvent] = []
        artifacts: list[ArtifactLink] = []
        building_inherited_capability = True
        try:
            payload: dict = {}

            def run_upstream() -> dict:
                nonlocal payload, building_inherited_capability
                payload = build_evidence_capability(
                    materials=self.materials,
                    request=request,
                    run_id=run_id,
                    stage_id=self.stage_id,
                    events=events,
                    artifacts=artifacts,
                )
                building_inherited_capability = False
                return payload

            request_context = request.messages[-1].content
            execution = executor.execute_job_agent_case(
                request_context,
                events=events,
                upstream_runner=run_upstream,
            )
            request_context = payload.get("request_context") or request_context
            candidate_response = execution["message"]
            candidate_path = persistent_artifact_path(self.stage_id, "runs", run_id, "candidate.json")
            model_details = payload.get("model") or {}
            model_io = model_details.get("model_io") or {}
            actual_source = (
                "model"
                if model_details.get("claim_source") == "model"
                and model_io.get("output_source") == "model"
                else "fixture"
            )
            write_json(
                candidate_path,
                {
                    "run_id": run_id,
                    "stage": self.stage_id,
                    "workspace_id": request.workspace_id,
                    "request": request_context,
                    "request_context": request_context,
                    "latest_user_request": request.messages[-1].content,
                    "candidate_response": candidate_response,
                    "response_status": execution["status"],
                    "evidence_notes": execution["evidence_notes"],
                    "trajectory": _compact_candidate_trajectory(execution["trajectory"]),
                    "candidate_mode": "live" if actual_source == "model" else "fixture",
                    "candidate_source": actual_source,
                    "candidate_attempted_mode": model_details.get("mode"),
                    "candidate_output_source": model_io.get("output_source"),
                    "lineage": {
                        "evidence_report": next(
                            (
                                artifact.path
                                for artifact in artifacts
                                if artifact.label == "Evidence report"
                            ),
                            None,
                        ),
                        "stage_run_id": run_id,
                    },
                },
            )
            artifacts.append(
                ArtifactLink(
                    label="Candidate response",
                    path=relative_artifact_path(candidate_path),
                )
            )
            latest_path = artifact_path(self.stage_id, "workspaces", request.workspace_id, "latest.json")
            write_json(
                latest_path,
                {
                    **payload,
                    "eval_target_run_id": run_id,
                },
            )
        except Exception as exc:
            # Only failures raised while composing the inherited Lab 4
            # capability keep Lab 4's prompt/Skill/context/MCP identity. Once
            # that call returns, later Lab 5 failures belong to Lab 5 itself.
            inherited_failure = building_inherited_capability
            component, operation = (
                failure_location(exc)
                if inherited_failure
                else ("labs.lab_05.web_adapter", "chat")
            )
            event_type = (
                "capability_boundary"
                if inherited_failure and isinstance(exc, MCPBoundaryError)
                else FAILURE_EVENT_TYPES.get(operation, "evidence")
                if inherited_failure
                else "trace"
            )
            events.append(
                HarnessEvent(
                    sequence=len(events) + 1,
                    type=event_type,
                    status="failed",
                    component=component,
                    operation=operation,
                    summary=f"Lab 5 complete run failed with {type(exc).__name__}",
                    details={"stage": self.stage_id},
                )
            )
            trace = write_run_trace(self.stage_id, run_id, events)
            raise StageExecutionError(exc, events, [*artifacts, trace], run_id) from exc

        trace = record_run_trace(self.stage_id, run_id, events)
        return ChatResponse(
            status="ok",
            stage=self.stage_id,
            run_id=run_id,
            assistant_message=candidate_response,
            events=events,
            state_summary={
                "structured_report": payload["structured_report"],
                "task_state": payload["task_state"],
                "claims": payload["claims"],
                "model": payload["model"],
                "skill": payload["skill"],
                "mcp_boundary": payload["mcp_boundary"],
                "response_status": execution["status"],
                "eval_target_run_id": run_id,
                "candidate_run_id": run_id,
                "limitations": ["Run the eval suite to measure behavior across cases"],
            },
            artifacts=[*artifacts, trace],
        )

    def run_eval(self, workspace_id: str) -> EvalResponse:
        run_id = f"run_{uuid.uuid4().hex}"
        events: list[HarnessEvent] = []
        artifacts: list[ArtifactLink] = []
        try:
            summary = build_eval_capability(
                records=self.materials.context(workspace_id),
                run_id=run_id,
                stage_id=self.stage_id,
                events=events,
                artifacts=artifacts,
            )
            latest_path = artifact_path(self.stage_id, "workspaces", workspace_id, "latest.json")
            write_json(latest_path, summary)
        except Exception as exc:
            events.append(
                HarnessEvent(
                    sequence=len(events) + 1,
                    type="eval",
                    status="failed",
                    component="labs.lab_05.src.evals",
                    operation="run_eval",
                    summary=f"Eval run failed with {type(exc).__name__}",
                    details={"stage": self.stage_id},
                )
            )
            trace = write_run_trace(self.stage_id, run_id, events)
            raise StageExecutionError(exc, events, [*artifacts, trace], run_id) from exc

        trace = write_run_trace(self.stage_id, run_id, events)
        return EvalResponse(
            status="ok",
            stage=self.stage_id,
            run_id=run_id,
            events=events,
            artifacts=[*artifacts, trace],
            summary=summary,
        )


def _compact_candidate_trajectory(trajectory: list[dict]) -> list[dict]:
    """Keep candidate lineage inspectable without duplicating the full trace."""
    compact: list[dict] = []
    for event in trajectory:
        if event.get("operation") not in _CANDIDATE_CHECKPOINTS:
            continue
        details = event.get("details") or {}
        compact.append(
            {
                "operation": event["operation"],
                "status": event["status"],
                "details": {
                    key: details[key]
                    for key in _CANDIDATE_DETAIL_FIELDS
                    if key in details
                },
            }
        )
    return compact


def build_eval_capability(
    *,
    records: list[dict],
    run_id: str,
    stage_id: str,
    events: list[HarnessEvent],
    artifacts: list[ArtifactLink],
) -> dict:
    """Run the component/run-level Lab 5 suite against the current implementation."""
    from labs.lab_05.src.evals import run_eval

    tasks_path = ROOT_DIR / "labs" / "lab_05" / "evals" / "tasks.jsonl"
    output_path = artifact_path(stage_id, "runs", run_id, "eval_summary.json")
    summary = run_eval(tasks_path, output_path=output_path)
    summary["eval_kind"] = "component_run_level_regression"
    summary["run"] = {
        "run_id": run_id,
        "stage": stage_id,
        "material_ids": [record["material_id"] for record in records],
    }
    write_json(output_path, summary)
    for result in summary["results"]:
        for runtime_event in result["response"]["trajectory"]:
            case_sequence = runtime_event["sequence"]
            events.append(
                HarnessEvent.model_validate(
                    {
                        **runtime_event,
                        "sequence": len(events) + 1,
                        "details": {
                            **runtime_event.get("details", {}),
                            "task_id": result["task_id"],
                            "case_sequence": case_sequence,
                        },
                    }
                )
            )
        events.append(
            HarnessEvent(
                sequence=len(events) + 1,
                type="eval",
                status="completed" if result["passed"] else "failed",
                component="labs.lab_05.src.evals",
                operation="analyze_trajectory",
                summary=f"{result['task_id']} · {'pass' if result['passed'] else 'fail'}",
                details={
                    "task_id": result["task_id"],
                    "output_passed": result["output_passed"],
                    "trajectory_passed": result["trajectory_passed"],
                    "passed": result["passed"],
                    "first_divergence": result["first_divergence"],
                    "reason": result["reason"],
                },
            )
        )
    artifacts.append(
        ArtifactLink(
            label="Eval summary",
            path=relative_artifact_path(output_path),
        )
    )
    return summary
