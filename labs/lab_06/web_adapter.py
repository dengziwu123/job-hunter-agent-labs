from __future__ import annotations

import json
import uuid
from time import perf_counter

from labs.lab_02.web_adapter import build_structured_capability
from labs.lab_03.web_adapter import (
    TaskStateLoadError,
    advance_and_save_task_state,
    fetch_pending_web_sources,
    latest_user_request,
    load_task_state,
    requested_action_type,
    source_items_from_materials,
    stateful_request_context,
)
from labs.lab_04.src.mcp_client_adapter import MCPBoundaryError
from labs.lab_04.src.skill_loader import skill_prompt_block
from labs.lab_04.web_adapter import load_student_skill
from labs.lab_05.web_adapter import build_eval_capability
from labs.shared.artifacts import artifact_path, relative_artifact_path, write_json
from labs.shared.web.contracts import ArtifactLink, ChatRequest, ChatResponse, EvalResponse, HarnessEvent
from labs.shared.web.errors import StageExecutionError
from labs.shared.web.materials import MaterialStore
from labs.shared.web.tracing import record_run_trace, write_run_trace


class Lab06Adapter:
    stage_id = "lab_06"

    def __init__(self, materials: MaterialStore) -> None:
        self.materials = materials

    def chat(self, request: ChatRequest) -> ChatResponse:
        run_id = f"run_{uuid.uuid4().hex}"
        trace_path = artifact_path(self.stage_id, "runs", run_id, "multi_agent_trace.jsonl")
        events: list[HarnessEvent] = []
        artifacts: list[ArtifactLink] = []
        try:
            payload = build_multi_agent_capability(
                materials=self.materials,
                request=request,
                run_id=run_id,
                stage_id=self.stage_id,
                events=events,
                artifacts=artifacts,
                trace_path=trace_path,
            )
            latest_path = artifact_path(self.stage_id, "workspaces", request.workspace_id, "latest.json")
            write_json(latest_path, payload)
        except Exception as exc:
            has_agent_trace = trace_path.is_file()
            base_sequence = len(events)
            events.extend(
                event.model_copy(update={"sequence": base_sequence + index})
                for index, event in enumerate(trace_to_events(trace_path), start=1)
            )
            state_load_failed = isinstance(exc, TaskStateLoadError)
            mcp_failed = isinstance(exc, MCPBoundaryError)
            prompt_failure_recorded = bool(
                events
                and events[-1].status == "failed"
                and events[-1].operation == "load_task_prompt"
            )
            if state_load_failed:
                failure_component = "labs.lab_03.src.state_store"
                failure_operation = "load_task_state"
            elif mcp_failed:
                failure_component = exc.component
                failure_operation = exc.operation
            elif has_agent_trace:
                failure_component = "labs.lab_06.agents.coordinator"
                failure_operation = "run_workflow"
            else:
                failure_component = "labs.lab_06.web_adapter"
                failure_operation = "execute_complete_stage"
            if not prompt_failure_recorded:
                events.append(
                    HarnessEvent(
                        sequence=len(events) + 1,
                        type=(
                            "capability_boundary"
                            if mcp_failed
                            else "handoff"
                            if has_agent_trace
                            else "stage"
                        ),
                        status="failed",
                        component=failure_component,
                        operation=failure_operation,
                        summary=f"Lab 6 complete run failed with {type(exc).__name__}",
                        details={"stage": self.stage_id},
                    )
                )
            if has_agent_trace:
                artifacts.append(
                    ArtifactLink(
                        label="Partial multi-agent trace",
                        path=relative_artifact_path(trace_path),
                    )
                )
            trace = write_run_trace(self.stage_id, run_id, events)
            raise StageExecutionError(exc, events, [*artifacts, trace], run_id) from exc

        events.append(
            HarnessEvent(
                sequence=len(events) + 1,
                type="trace",
                status="completed",
                component="labs.lab_05.src.evals",
                operation="prepare_eval_target",
                summary="Registered the bounded multi-agent run as the current eval target",
                details={"target_run_id": run_id},
            )
        )
        trace = record_run_trace(self.stage_id, run_id, events)
        return ChatResponse(
            status="ok",
            stage=self.stage_id,
            run_id=run_id,
            assistant_message=(
                f"{payload['action']['content']}\n\n"
                f"Approval: {payload['approval']['status']}\n"
                f"Stop reason: {payload['stop_reason']}\n"
                f"Task state: revision {payload['task_state']['revision']}"
            ),
            events=events,
            state_summary={
                "roles": ["research", "summarize", "action"],
                "research": payload["research"],
                "fit_gap_report": payload["summary"]["fit_gap_report"],
                "task_state": payload["task_state"],
                "evidence_notes": payload["summary"].get("evidence_notes", []),
                "draft_action": payload["action"],
                "approval": payload["approval"],
                "budget": payload["budget"],
                "context_budget": payload["context_budget"],
                "action_context_budget": payload["action_context_budget"],
                "stop_reason": payload["stop_reason"],
                "limitations": ["Thin harness is educational runtime plumbing, not a mature platform"],
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


def append_protocol_trace_operations(trace_path, protocol_operations: list[dict]) -> None:
    """Record nested MCP/model operations at their actual coordinator position."""
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("a", encoding="utf-8") as handle:
        for protocol_operation in protocol_operations:
            handle.write(
                json.dumps(
                    {
                        "event": protocol_operation.get(
                            "event_type", "capability_boundary"
                        ),
                        "status": "completed",
                        "component": protocol_operation.get(
                            "component", "labs.lab_04.src.mcp_client_adapter.Client"
                        ),
                        "operation": protocol_operation["operation"],
                        "summary": protocol_operation["summary"],
                        "duration_ms": protocol_operation["duration_ms"],
                        **protocol_operation["details"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def build_multi_agent_capability(
    *,
    materials: MaterialStore,
    request: ChatRequest,
    run_id: str,
    stage_id: str,
    events: list[HarnessEvent],
    artifacts: list[ArtifactLink],
    trace_path,
) -> dict:
    """Compose the bounded multi-agent graph from raw inputs in one stage run."""
    from labs.lab_04.src.context_budget import render_evidence_sources
    from labs.lab_04.src.job_research import research_job_board
    from labs.lab_04.src.prompt_loader import load_task_prompt
    from labs.lab_04.src.retrieval import SourceRecord
    from labs.lab_06.agents.coordinator import run_workflow

    user_request = latest_user_request(request)
    previous_state, discarded_state = load_task_state(request, stage_id)
    events.append(
        HarnessEvent(
            sequence=len(events) + 1,
            type="state_load",
            status="completed",
            component="labs.lab_03.src.state_store",
            operation="load_task_state",
            summary=(
                "Discarded incompatible task state and started a new managed task state"
                if discarded_state
                else f"Resumed task state revision {previous_state.get('revision', 0)}"
                if previous_state
                else "Started a new managed task state"
            ),
            details={
                "state_id": previous_state.get("state_id") if previous_state else None,
                "revision": previous_state.get("revision", 0) if previous_state else 0,
                "message_count": len(request.messages),
                "discarded_incompatible_state": discarded_state,
            },
        )
    )
    request_context = stateful_request_context(request, previous_state)
    workflow_query = request_context
    records = materials.context(request.workspace_id)
    structured = build_structured_capability(
        records=records,
        user_request=request_context,
        raw_user_request=user_request,
        run_id=run_id,
        stage_id=stage_id,
        events=events,
        artifacts=artifacts,
    )
    fetch_pending_web_sources(
        materials=materials,
        workspace_id=request.workspace_id,
        records=records,
        events=events,
    )
    records = materials.context(request.workspace_id)
    source_items = source_items_from_materials(records)
    skill = load_student_skill()
    events.append(
        HarnessEvent(
            sequence=len(events) + 1,
            type="skill",
            status="completed",
            component="labs.lab_04.skills.job-prep.SKILL.md",
            operation="load_skill",
            summary="Loaded evidence and draft-only rules into the multi-agent run",
            details=skill.model_dump(),
        )
    )
    skill_prompt = skill_prompt_block(skill)
    started = perf_counter()
    try:
        task_prompt = load_task_prompt()
    except Exception as exc:
        events.append(
            HarnessEvent(
                sequence=len(events) + 1,
                type="prompt",
                status="failed",
                component="labs.lab_04.prompts.grounded-job-research.md",
                operation="load_task_prompt",
                summary=f"Lab 6 task-prompt loading failed with {type(exc).__name__}",
                duration_ms=max(1, int((perf_counter() - started) * 1000)),
                details={
                    "semantic_key": "prompt.load_task_template",
                    "error_type": type(exc).__name__,
                },
            )
        )
        raise
    events.append(
        HarnessEvent(
            sequence=len(events) + 1,
            type="prompt",
            status="completed",
            component="labs.lab_04.prompts.grounded-job-research.md",
            operation="load_task_prompt",
            summary="Loaded the grounded job-research prompt for the research agent's MCP boundary",
            duration_ms=max(1, int((perf_counter() - started) * 1000)),
            details={
                "semantic_key": "prompt.load_task_template",
                **task_prompt.model_dump(exclude={"template"}),
                "template": task_prompt.template,
            },
        )
    )
    evidence_sources = render_evidence_sources(
        [SourceRecord.model_validate(item) for item in source_items]
    )
    mcp_boundary: dict = {}

    def inspect_planned_query(planned_query: str) -> list[dict]:
        try:
            result = research_job_board(
                task_prompt=task_prompt,
                skill_rules=skill_prompt,
                user_request=planned_query,
                evidence_sources=evidence_sources,
            )
        except MCPBoundaryError as exc:
            append_protocol_trace_operations(trace_path, exc.completed_operations)
            raise
        mcp_protocol_operations = result.pop("protocol_operations")
        append_protocol_trace_operations(trace_path, mcp_protocol_operations)
        mcp_boundary.update(result)
        return [
            {
                "source_id": f"job-{record['ats']}-{record['job_id']}",
                "title": record["title"],
                "path": record["url"],
                "snippet": ". ".join(
                    value
                    for value in (
                        record["title"],
                        record.get("location", ""),
                        record.get("department", ""),
                        record.get("summary", ""),
                    )
                    if value
                ),
            }
            for record in result["records"]
        ]

    payload = run_workflow(
        profile=structured["profile"],
        job_description=structured["job_description"],
        source_items=source_items,
        prior_report=structured["report"],
        requested_action=requested_action_type(user_request),
        user_request=workflow_query,
        skill_prompt=skill_prompt,
        trace_path=trace_path,
        on_planned_query=inspect_planned_query,
    )
    trace_events = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    payload["trace_events"] = trace_events
    base_sequence = len(events)
    events.extend(
        event.model_copy(update={"sequence": base_sequence + index})
        for index, event in enumerate(trace_to_events(trace_path), start=1)
    )
    task_state, task_state_artifact_path = advance_and_save_task_state(
        previous_state,
        request=request,
        stage_id=stage_id,
        user_request=user_request,
        report=payload["summary"]["fit_gap_report"],
        source_ids=payload["context_budget"]["selected_source_ids"],
        action_status=payload["action"]["status"],
        run_id=run_id,
    )
    payload["task_state"] = task_state
    events.append(
        HarnessEvent(
            sequence=len(events) + 1,
            type="state_update",
            status="completed",
            component="labs.lab_03.src.state_store",
            operation="advance_task_state",
            summary=f"Saved managed task state revision {task_state['revision']}",
            details={
                "state_id": task_state["state_id"],
                "revision": task_state["revision"],
                "message_count": len(request.messages),
                "status": task_state["validation_status"],
            },
        )
    )
    payload["run"] = {
        "run_id": run_id,
        "stage": stage_id,
        "material_ids": [record["material_id"] for record in records],
    }
    payload["skill"] = skill.model_dump()
    payload["mcp_boundary"] = mcp_boundary
    output_path = artifact_path(stage_id, "runs", run_id, "multi_agent_result.json")
    write_json(output_path, payload)
    artifacts.extend(
        [
            ArtifactLink(
                label="Multi-agent result",
                path=relative_artifact_path(output_path),
            ),
            ArtifactLink(
                label="Multi-agent call trace",
                path=relative_artifact_path(trace_path),
            ),
            ArtifactLink(
                label="Managed task state",
                path=relative_artifact_path(task_state_artifact_path),
            ),
        ]
    )
    return payload


def trace_to_events(path) -> list[HarnessEvent]:
    if not path.is_file():
        return []
    raw_events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    events: list[HarnessEvent] = []
    for sequence, raw in enumerate(raw_events, start=1):
        event_type = raw.get("event", "trace")
        component = raw.get("component", "labs.lab_06.agents.coordinator")
        operation = raw.get("operation", event_type)
        status = raw.get("status", "completed")
        if status not in {"started", "completed", "failed", "blocked"}:
            status = "failed"
        summary = event_summary(raw)
        raw_duration = raw.get("duration_ms")
        duration_ms = (
            int(raw_duration)
            if isinstance(raw_duration, (int, float))
            and not isinstance(raw_duration, bool)
            and raw_duration > 0
            else None
        )
        details = {
            key: value
            for key, value in raw.items()
            if key
            not in {
                "event",
                "component",
                "operation",
                "status",
                "summary",
                "content",
                "duration_ms",
            }
        }
        events.append(
            HarnessEvent(
                sequence=sequence,
                type="handoff" if event_type in {"delegation", "handoff"} else event_type,
                status=status,
                component=component,
                operation=operation,
                summary=summary,
                duration_ms=duration_ms,
                details=details,
            )
        )
    return events


def event_summary(event: dict) -> str:
    if isinstance(event.get("summary"), str):
        return event["summary"]
    event_type = event.get("event")
    if event_type == "contract_validation":
        direction = event.get("direction")
        if event.get("status") == "failed":
            if direction in {"input", "output"}:
                return f"The {event['agent']} role {direction} required-field validation failed"
            return f"The {event['agent']} role contract validation failed"
        if direction in {"input", "output"}:
            return f"Validated the {event['agent']} role {direction} required fields"
        return f"Validated the {event['agent']} role contract"
    if event_type in {"delegation", "handoff"}:
        return f"Called {event.get('to', event.get('agent', 'agent'))} with an explicit handoff"
    if event_type == "tool_call":
        return "Research called the existing Lab 3 source tool"
    if event_type == "tool_result":
        return f"Research returned {event.get('source_count', 0)} sources"
    if event_type == "context_budget":
        if event.get("agent") == "action":
            return (
                f"Action measured {event.get('estimated_tokens', 0)} of "
                f"{event.get('budget_tokens', 0)} available prompt tokens"
            )
        return (
            f"Research fit {event.get('source_count_after', 0)} of "
            f"{event.get('source_count_before', 0)} sources into the context budget"
        )
    if event_type == "model_call":
        return f"{event.get('agent', 'Agent').title()} used its role instruction"
    if event_type == "summary_output":
        return "Summarize returned the Lab 2 report and Lab 4 evidence contracts"
    if event_type == "action_draft":
        return f"Action returned {event.get('status', 'a draft')}"
    if event_type == "approval_decision":
        return f"Approval guardrail returned {event.get('status')}"
    if event_type == "model_usage":
        return f"Used {event.get('model_calls', 0)} Lab 6 role-owned model calls"
    if event_type == "stop":
        return f"Coordinator stopped: {event.get('reason')}"
    return event_type.replace("_", " ").title()
