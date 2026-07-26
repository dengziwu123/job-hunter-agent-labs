from __future__ import annotations

import json
import uuid
from time import perf_counter

from pydantic import ValidationError

from labs.shared.artifacts import artifact_path, write_json
from labs.shared.config import ROOT_DIR, load_settings
from labs.shared.web.material_inputs import (
    active_material,
    material_to_job_description,
    material_to_profile,
)
from labs.shared.web.contracts import ArtifactLink, ChatRequest, ChatResponse, HarnessEvent
from labs.shared.web.errors import StageExecutionError
from labs.shared.web.materials import MaterialStore
from labs.shared.web.tracing import write_run_trace


class Lab02Adapter:
    stage_id = "lab_02"

    def __init__(self, materials: MaterialStore) -> None:
        self.materials = materials

    def chat(self, request: ChatRequest) -> ChatResponse:
        from labs.lab_02.src.run_structured import GeminiStructuredReportClient, run_from_objects

        run_id = f"run_{uuid.uuid4().hex}"
        records = self.materials.context(request.workspace_id)
        started = perf_counter()

        try:
            profile_record = active_material(records, "candidate_profile")
            jd_record = active_material(records, "job_description")
            profile = material_to_profile(profile_record)
            job_description = material_to_job_description(jd_record)
            report_path = artifact_path(self.stage_id, "runs", run_id, "job_prep_report.json")
            report_client = GeminiStructuredReportClient(load_settings())
            payload = run_from_objects(
                profile,
                job_description,
                client=report_client,
                output_path=report_path,
            )
            payload["run"] = {
                "run_id": run_id,
                "stage": self.stage_id,
                "derived_from": [profile_record["material_id"], jd_record["material_id"]],
            }
            write_json(report_path, payload)
            latest_path = artifact_path(self.stage_id, "workspaces", request.workspace_id, "latest.json")
            write_json(latest_path, payload)
        except Exception as exc:
            duration_ms = int((perf_counter() - started) * 1000)
            component, operation = failure_location(exc)
            events = [
                HarnessEvent(
                    sequence=1,
                    type="validation" if isinstance(exc, ValidationError) else "model_call",
                    status="failed",
                    component=component,
                    operation=operation,
                    summary=f"Lab 2 cumulative run failed with {type(exc).__name__}",
                    duration_ms=duration_ms,
                    details={"reuses": "Lab 1 ModelClient.complete"},
                )
            ]
            trace = write_run_trace(self.stage_id, run_id, events)
            raise StageExecutionError(exc, events, [trace], run_id) from exc

        duration_ms = int((perf_counter() - started) * 1000)
        report = payload["report"]
        events = [
            HarnessEvent(
                sequence=1,
                type="model_call",
                status="completed",
                component="labs.lab_01.src.model_client.ModelClient",
                operation="complete",
                summary="Reused the Lab 1 model boundary to request report JSON",
                duration_ms=duration_ms,
                details={
                    "called_by": "labs.lab_02.src.run_structured.GeminiStructuredReportClient",
                    "profile_id": profile.id,
                    "job_description_id": job_description.id,
                    "response_fields": sorted(report),
                },
            ),
            HarnessEvent(
                sequence=2,
                type="validation",
                status="completed",
                component="labs.lab_02.src.schemas",
                operation="validate_fit_gap_report",
                summary="Validated required FitGapReport fields and types",
                details={"schema": "FitGapReport", "status": payload["validation"]["status"]},
            ),
            HarnessEvent(
                sequence=3,
                type="state_update",
                status="completed",
                component="labs.lab_02.src.state_store",
                operation="mark_report_generated",
                summary="Persisted the validated report in JobPrepState",
                details={
                    "from": "started",
                    "to": payload["state"]["status"],
                    "artifact": payload["state"]["latest_report_artifact"],
                },
            ),
        ]
        trace = write_run_trace(self.stage_id, run_id, events)
        relative_report = report_path.relative_to(ROOT_DIR / "artifacts").as_posix()
        return ChatResponse(
            status="ok",
            stage=self.stage_id,
            run_id=run_id,
            assistant_message=format_report(report),
            events=events,
            state_summary={
                "profile": payload["profile"],
                "job_description": payload["job_description"],
                "report": report,
                "task_state": payload["state"],
                "validation": payload["validation"],
                "limitations": [
                    "No tool access",
                    "No external action boundary",
                    "No claim-level evidence",
                ],
            },
            artifacts=[
                ArtifactLink(label="Validated fit/gap report", path=relative_report),
                trace,
            ],
        )

    def run_eval(self, _: str) -> None:
        raise NotImplementedError("Eval is introduced in Lab 5.")


def failure_location(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, ValidationError):
        return "labs.lab_02.src.schemas", "validate_fit_gap_report"
    if isinstance(exc, json.JSONDecodeError):
        return "labs.lab_02.src.run_structured.GeminiStructuredReportClient", "generate_report_json"
    if isinstance(exc, NotImplementedError):
        return "labs.lab_02.src.run_structured.GeminiStructuredReportClient", "generate_report_json"
    return "labs.lab_02.src.run_structured", "run_from_objects"


def format_report(report: dict) -> str:
    sections = [report["fit_summary"]]
    labels = [
        ("Strengths", "strengths"),
        ("Gaps", "gaps"),
        ("Missing information", "missing_info"),
        ("Recommended next steps", "recommended_next_steps"),
    ]
    for label, key in labels:
        values = report.get(key) or []
        sections.append(f"{label}:\n" + "\n".join(f"• {value}" for value in values))
    return "\n\n".join(sections)
