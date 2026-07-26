from __future__ import annotations

import inspect
import json
import uuid
from time import perf_counter

from pydantic import ValidationError

from labs.shared.artifacts import artifact_path, relative_artifact_path, write_json
from labs.shared.config import Settings, load_settings
from labs.shared.web.material_inputs import (
    active_material,
    material_to_job_description,
    material_to_profile,
)
from labs.shared.web.contracts import ArtifactLink, ChatRequest, ChatResponse, HarnessEvent
from labs.shared.web.errors import StageExecutionError
from labs.shared.web.materials import MaterialStore
from labs.shared.web.model_io import model_io_details
from labs.shared.web.tracing import write_run_trace


class ModelIoRecorder:
    def __init__(self, delegate, *, settings: Settings) -> None:
        self.delegate = delegate
        self.settings = settings
        self.messages: list[dict[str, str]] = []
        self.raw_model_output: str | None = None

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.messages = messages
        self.raw_model_output = self.delegate.complete(messages)
        return self.raw_model_output

    def details(self, user_request: str) -> dict[str, str]:
        if not self.messages:
            return {}
        return model_io_details(
            self.settings,
            self.messages,
            user_request,
            self.raw_model_output,
        )


class Lab02Adapter:
    stage_id = "lab_02"

    def __init__(self, materials: MaterialStore) -> None:
        self.materials = materials

    def chat(self, request: ChatRequest) -> ChatResponse:
        run_id = f"run_{uuid.uuid4().hex}"
        records = self.materials.context(request.workspace_id)
        events: list[HarnessEvent] = []
        artifacts: list[ArtifactLink] = []
        user_request = next(
            message.content
            for message in reversed(request.messages)
            if message.role == "user"
        )

        try:
            payload = build_structured_capability(
                records=records,
                user_request=user_request,
                run_id=run_id,
                stage_id=self.stage_id,
                events=events,
                artifacts=artifacts,
            )
            latest_path = artifact_path(self.stage_id, "workspaces", request.workspace_id, "latest.json")
            write_json(latest_path, payload)
        except Exception as exc:
            component, operation = failure_location(exc)
            validation_failure = isinstance(exc, (ValidationError, json.JSONDecodeError))
            events.append(
                HarnessEvent(
                    sequence=len(events) + 1,
                    type="validation" if validation_failure else "model_call",
                    status="failed",
                    component=component,
                    operation=operation,
                    summary=f"Lab 2 complete run failed with {type(exc).__name__}",
                    details={"stage": self.stage_id},
                )
            )
            trace = write_run_trace(self.stage_id, run_id, events)
            raise StageExecutionError(exc, events, [*artifacts, trace], run_id) from exc

        report = payload["report"]
        trace = write_run_trace(self.stage_id, run_id, events)
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
            artifacts=[*artifacts, trace],
        )

    def run_eval(self, _: str) -> None:
        raise NotImplementedError("Eval is introduced in Lab 5.")


def build_structured_capability(
    *,
    records: list[dict],
    user_request: str,
    raw_user_request: str | None = None,
    run_id: str,
    stage_id: str,
    events: list[HarnessEvent],
    artifacts: list[ArtifactLink],
) -> dict:
    """Run the Lab 1/2 model, schema, and state primitives inside one stage run."""
    from labs.lab_01.src.model_client import ModelClient
    from labs.lab_02.src.run_structured import GeminiStructuredReportClient, run_from_objects

    profile_record = active_material(records, "candidate_profile")
    jd_record = active_material(records, "job_description")
    profile = material_to_profile(profile_record)
    job_description = material_to_job_description(jd_record)
    filename = "job_prep_report.json" if stage_id == "lab_02" else "structured_report.json"
    report_path = artifact_path(stage_id, "runs", run_id, filename)
    settings = load_settings()
    model_io_recorder = ModelIoRecorder(
        ModelClient(settings),
        settings=settings,
    )
    report_client = GeminiStructuredReportClient(
        settings,
        model_client=model_io_recorder,  # type: ignore[arg-type]
    )
    started = perf_counter()
    run_parameters = inspect.signature(run_from_objects).parameters
    run_kwargs = {
        "client": report_client,
        "output_path": report_path,
    }
    if "user_request" in run_parameters:
        run_kwargs["user_request"] = user_request
    try:
        payload = run_from_objects(
            profile,
            job_description,
            **run_kwargs,
        )
    except Exception:
        duration_ms = int((perf_counter() - started) * 1000)
        model_io = model_io_recorder.details(raw_user_request or user_request)
        if model_io_recorder.messages:
            model_completed = model_io_recorder.raw_model_output is not None
            events.append(
                HarnessEvent(
                    sequence=len(events) + 1,
                    type="model_call",
                    status="completed" if model_completed else "failed",
                    component="labs.lab_01.src.model_client.ModelClient",
                    operation="complete",
                    summary=(
                        "The model returned output before structured parsing or validation failed"
                        if model_completed
                        else "The provider failed after receiving the recorded model input"
                    ),
                    duration_ms=duration_ms,
                    details=model_io,
                )
            )
        raise
    duration_ms = int((perf_counter() - started) * 1000)
    model_io = model_io_recorder.details(raw_user_request or user_request)
    payload["run"] = {
        "run_id": run_id,
        "stage": stage_id,
        "material_ids": [profile_record["material_id"], jd_record["material_id"]],
    }
    write_json(report_path, payload)
    report = payload["report"]
    events.extend(
        [
            HarnessEvent(
                sequence=len(events) + 1,
                type="model_call",
                status="completed",
                component="labs.lab_01.src.model_client.ModelClient",
                operation="complete",
                summary="Called the Lab 1 model boundary to request report JSON",
                duration_ms=duration_ms,
                details={
                    "called_by": "labs.lab_02.src.run_structured.GeminiStructuredReportClient",
                    "profile_id": profile.id,
                    "job_description_id": job_description.id,
                    "input_fields": ["candidate_profile", "job_description", "user_request"],
                    "response_fields": sorted(report),
                    **model_io,
                    "validated_output": json.dumps(report, ensure_ascii=False, indent=2),
                },
            ),
            HarnessEvent(
                sequence=len(events) + 2,
                type="validation",
                status="completed",
                component="labs.lab_02.src.schemas",
                operation="validate_fit_gap_report",
                summary="Validated required FitGapReport fields and types",
                details={"schema": "FitGapReport", "status": payload["validation"]["status"]},
            ),
            HarnessEvent(
                sequence=len(events) + 3,
                type="state_update",
                status="completed",
                component="labs.lab_02.src.state_store",
                operation="mark_report_generated",
                summary="Stored validated state inside the current stage run",
                details={
                    "from": "started",
                    "to": payload["state"]["status"],
                    "artifact": payload["state"]["latest_report_artifact"],
                },
            ),
        ]
    )
    artifacts.append(
        ArtifactLink(
            label="Validated fit/gap report",
            path=relative_artifact_path(report_path),
        )
    )
    return payload


def failure_location(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, ValidationError):
        return "labs.lab_02.src.schemas", "validate_fit_gap_report"
    if isinstance(exc, json.JSONDecodeError):
        return "labs.lab_02.src.run_structured.GeminiStructuredReportClient", "generate_report_json"
    if isinstance(exc, NotImplementedError):
        return "labs.lab_02.src.run_structured.GeminiStructuredReportClient", "generate_report_json"
    return "labs.lab_02.src.run_structured", "run_from_objects"


def format_report(report: dict) -> str:
    sections = [f"Fit Summary:\n{report['fit_summary']}"]
    labels = [
        ("Strengths", "strengths"),
        ("Gaps", "gaps"),
        ("Risks", "risks"),
        ("Missing information", "missing_info"),
        ("Recommended next steps", "recommended_next_steps"),
    ]
    for label, key in labels:
        values = report.get(key) or []
        sections.append(f"{label}:\n" + "\n".join(f"• {value}" for value in values))
    return "\n\n".join(sections)
