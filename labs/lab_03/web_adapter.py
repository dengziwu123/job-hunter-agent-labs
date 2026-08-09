from __future__ import annotations

import json
import uuid
from pathlib import Path
from time import perf_counter

from labs.lab_02.web_adapter import build_structured_capability
from labs.shared.artifacts import (
    artifact_path,
    read_json,
    relative_artifact_path,
    task_state_path,
    task_state_revision_path,
    write_json,
)
from labs.shared.web.contracts import ArtifactLink, ChatRequest, ChatResponse, HarnessEvent
from labs.shared.web.errors import StageExecutionError
from labs.shared.web.materials import MaterialStore
from labs.shared.web.tracing import write_run_trace


MAX_CONTEXT_USER_REQUESTS = 20
MAX_CONTEXT_CHARACTERS = 20_000


class TaskStateLoadError(RuntimeError):
    pass


class Lab03Adapter:
    stage_id = "lab_03"

    def __init__(self, materials: MaterialStore) -> None:
        self.materials = materials

    def chat(self, request: ChatRequest) -> ChatResponse:
        run_id = f"run_{uuid.uuid4().hex}"
        events: list[HarnessEvent] = []
        artifacts: list[ArtifactLink] = []

        try:
            payload = build_tool_action_capability(
                materials=self.materials,
                request=request,
                run_id=run_id,
                stage_id=self.stage_id,
                events=events,
                artifacts=artifacts,
            )
            latest_path = artifact_path(self.stage_id, "workspaces", request.workspace_id, "latest.json")
            write_json(latest_path, payload)
        except Exception as exc:
            component, operation = failure_location(exc)
            events.append(
                HarnessEvent(
                    sequence=len(events) + 1,
                    type="tool_call" if isinstance(exc, NotImplementedError) else "stage",
                    status="failed",
                    component=component,
                    operation=operation,
                    summary=f"Lab 3 complete run failed with {type(exc).__name__}",
                    details={"stage": self.stage_id},
                )
            )
            trace = write_run_trace(self.stage_id, run_id, events)
            raise StageExecutionError(exc, events, [*artifacts, trace], run_id) from exc

        trace = write_run_trace(self.stage_id, run_id, events)
        action = payload["action"]
        return ChatResponse(
            status="ok",
            stage=self.stage_id,
            run_id=run_id,
            assistant_message=(
                f"{payload['draft_text']}\n\n"
                f"Action status: {action['status']}\n"
                f"Reason: {action['reason']}\n"
                f"Task state: revision {payload['task_state']['revision']}"
            ),
            events=events,
            state_summary={
                "structured_report": payload["structured_report"],
                "task_state": payload["task_state"],
                "tool_request": payload["tool_request"],
                "sources": payload["sources"],
                "draft_action": action,
                "limitations": ["Retrieved sources are not yet claim-level evidence"],
            },
            artifacts=[*artifacts, trace],
        )

    def run_eval(self, _: str) -> None:
        raise NotImplementedError("Eval is introduced in Lab 5.")


def build_tool_action_capability(
    *,
    materials: MaterialStore,
    request: ChatRequest,
    run_id: str,
    stage_id: str,
    events: list[HarnessEvent],
    artifacts: list[ArtifactLink],
) -> dict:
    """Compose Lab 1-3 primitives inside one stage run."""
    from labs.lab_03.src.model_tool_use import ToolUseModel
    from labs.lab_03.src.policies import draft_action
    from labs.lab_03.src.tools import search_sources

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
    model = ToolUseModel()

    started = perf_counter()
    tool_request = model.request_tool_call(request_context)
    tool_call_duration_ms = max(1, int((perf_counter() - started) * 1000))

    started = perf_counter()
    sources = search_sources(
        tool_request["query"],
        limit=tool_request.get("limit") or 2,
        source_items=source_items_from_materials(records),
    )
    search_duration_ms = max(1, int((perf_counter() - started) * 1000))

    started = perf_counter()
    draft_text = model.draft_outreach(request_context, sources)
    draft_duration_ms = max(1, int((perf_counter() - started) * 1000))

    started = perf_counter()
    action = draft_action(
        requested_action_type(user_request),
        f"{user_request}\n\nDraft:\n{draft_text}",
    )
    action_duration_ms = max(1, int((perf_counter() - started) * 1000))

    base_sequence = len(events)
    events.extend(
        [
            HarnessEvent(
                sequence=base_sequence + 1,
                type="tool_call",
                status="completed",
                component="labs.lab_03.src.model_tool_use.ToolUseModel",
                operation="request_tool_call",
                summary="The model selected a search_sources request",
                duration_ms=tool_call_duration_ms,
                details={
                    "mode": model.mode,
                    "source": model.tool_call_source,
                    "tool": "search_sources",
                    "input": tool_request,
                    **model.tool_call_io,
                },
            ),
            HarnessEvent(
                sequence=base_sequence + 2,
                type="tool_result",
                status="completed",
                component="labs.lab_03.src.tools",
                operation="search_sources",
                summary=f"Returned {len(sources)} current run sources",
                duration_ms=search_duration_ms,
                details={
                    "tool": "search_sources",
                    "source_ids": [source.source_id for source in sources],
                },
            ),
            HarnessEvent(
                sequence=base_sequence + 3,
                type="model_call",
                status="completed",
                component="labs.lab_03.src.model_tool_use.ToolUseModel",
                operation="draft_outreach",
                summary="Drafted text from the executed tool results",
                duration_ms=draft_duration_ms,
                details={
                    "model_calls": model.calls,
                    "draft_characters": len(draft_text),
                    **model.draft_io,
                },
            ),
            HarnessEvent(
                sequence=base_sequence + 4,
                type="guardrail",
                status="blocked" if action.status == "blocked" else "completed",
                component="labs.lab_03.src.policies",
                operation="classify_action",
                summary=f"Action policy returned {action.status}",
                duration_ms=action_duration_ms,
                details={
                    "action_type": action.action_type,
                    "status": action.status,
                    "reason": action.reason,
                },
            ),
        ]
    )
    task_state, task_state_artifact_path = advance_and_save_task_state(
        request=request,
        stage_id=stage_id,
        previous_state=previous_state,
        user_request=user_request,
        report=structured["report"],
        source_ids=[source.source_id for source in sources],
        action_status=action.status,
        run_id=run_id,
    )
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
    output_path = artifact_path(stage_id, "runs", run_id, "tool_action_run.json")
    payload = {
        "run_id": run_id,
        "stage": stage_id,
        "material_ids": [record["material_id"] for record in records],
        "profile": structured["profile"],
        "job_description": structured["job_description"],
        "structured_report": structured["report"],
        "task_state": task_state,
        # Lab 3 owns the bounded conversation context. Publishing it here lets a
        # later Lab put the same string in front of its own model instead of
        # recomputing it from a task state this call has already advanced.
        "request_context": request_context,
        "tool_request": tool_request,
        "tool_call_source": model.tool_call_source,
        "sources": [source.model_dump() for source in sources],
        "draft_text": draft_text,
        "action": action.model_dump(),
    }
    write_json(output_path, payload)
    artifacts.append(
        ArtifactLink(
            label="Tool and action run",
            path=relative_artifact_path(output_path),
        )
    )
    artifacts.append(
        ArtifactLink(
            label="Managed task state",
            path=relative_artifact_path(task_state_artifact_path),
        )
    )
    return payload


def latest_user_request(request: ChatRequest) -> str:
    for message in reversed(request.messages):
        if message.role == "user":
            return message.content
    raise ValueError("A Lab run requires at least one user message.")


def load_task_state(request: ChatRequest, stage_id: str) -> tuple[dict | None, bool]:
    from labs.lab_03.src.state_store import ManagedTaskState

    state_path = task_state_path(stage_id, request.workspace_id)
    if not state_path.is_file():
        return None, False
    try:
        state = ManagedTaskState.model_validate(read_json(state_path)).model_dump()
    except ValueError:
        return None, True
    except OSError as exc:
        raise TaskStateLoadError("Could not read persisted managed task state.") from exc
    return state, False


def stateful_request_context(
    request: ChatRequest,
    previous_state: dict | None,
) -> str:
    user_requests = [
        message.content
        for message in request.messages
        if message.role == "user"
    ]
    latest_request = user_requests[-1][:MAX_CONTEXT_CHARACTERS]
    earlier_requests = bounded_prior_user_requests(
        user_requests[:-1],
        MAX_CONTEXT_CHARACTERS - len(latest_request),
    )
    persisted = "No prior managed task state."
    if previous_state is not None:
        persisted = json.dumps(
            {
                "state_id": previous_state["state_id"],
                "revision": previous_state["revision"],
                "source_ids": previous_state["source_ids"][-20:],
                "action_status": previous_state["action_status"],
                "validation_status": previous_state["validation_status"],
            },
            ensure_ascii=False,
        )
    earlier_context = "\n".join(f"USER: {item}" for item in earlier_requests)
    return (
        "Continue the current job-preparation task using the bounded user-request "
        "history and managed-state metadata below. Regenerate the report from the "
        "raw profile/JD and user requests; do not copy a prior model report.\n\n"
        f"Managed task state metadata:\n{persisted}\n\n"
        f"Earlier user requests:\n{earlier_context or 'None.'}\n\n"
        f"Latest user request:\n{latest_request}"
    )


def bounded_prior_user_requests(user_requests: list[str], character_budget: int) -> list[str]:
    remaining = character_budget
    selected: list[str] = []
    for content in reversed(user_requests[-MAX_CONTEXT_USER_REQUESTS:]):
        if remaining <= 0:
            break
        excerpt = content[:remaining]
        selected.append(excerpt)
        remaining -= len(excerpt)
    return list(reversed(selected))


def advance_and_save_task_state(
    previous_state: dict | None,
    *,
    request: ChatRequest,
    stage_id: str,
    user_request: str,
    report: dict,
    source_ids: list[str],
    action_status: str,
    run_id: str,
) -> tuple[dict, Path]:
    from labs.lab_03.src.state_store import advance_task_state

    task_state = advance_task_state(
        previous_state,
        user_request=user_request,
        report=report,
        source_ids=source_ids,
        action_status=action_status,
        run_id=run_id,
    ).model_dump()
    write_json(task_state_path(stage_id, request.workspace_id), task_state)
    revision_path = task_state_revision_path(
        stage_id,
        request.workspace_id,
        task_state["revision"],
    )
    write_json(revision_path, task_state)
    return task_state, revision_path


def fetch_pending_web_sources(
    *,
    materials: MaterialStore,
    workspace_id: str,
    records: list[dict],
    events: list[HarnessEvent],
) -> None:
    from labs.shared.web.web_fetch import fetch_web_page

    for record in pending_web_sources(records):
        requested_url = record["source_url"]
        try:
            page = fetch_web_page(requested_url)
            completed = materials.complete_web_source(
                workspace_id,
                record["material_id"],
                title=page.title,
                final_url=page.url,
                text=page.text,
            )
        except Exception as exc:
            events.append(
                HarnessEvent(
                    sequence=len(events) + 1,
                    type="tool_call",
                    status="failed",
                    component="labs.shared.web.web_fetch",
                    operation="fetch_web_page",
                    summary=f"Web fetch failed with {type(exc).__name__}",
                    details={"requested_url": requested_url},
                )
            )
            raise
        base_sequence = len(events)
        events.extend(
            [
                HarnessEvent(
                    sequence=base_sequence + 1,
                    type="tool_call",
                    status="completed",
                    component="labs.shared.web.web_fetch",
                    operation="fetch_web_page",
                    summary=f"Fetched public webpage: {page.title}",
                    details={
                        "tool": "fetch_web_page",
                        "requested_url": requested_url,
                        "final_url": page.url,
                    },
                ),
                HarnessEvent(
                    sequence=base_sequence + 2,
                    type="tool_result",
                    status="completed",
                    component="labs.shared.web.materials.MaterialStore",
                    operation="complete_web_source",
                    summary="Stored readable webpage text in the current run",
                    details={
                        "tool": "fetch_web_page",
                        "material_id": completed.material_id,
                        "characters": completed.characters,
                    },
                ),
            ]
        )


def source_items_from_materials(records: list[dict]) -> list[dict]:
    items: list[dict] = []
    for record in records:
        if record["kind"] != "web_source" or record.get("status", "ready") != "ready":
            continue
        try:
            parsed = json.loads(record["text"])
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict) and {"source_id", "title", "snippet"} <= set(item):
                    items.append(
                        {
                            "source_id": item["source_id"],
                            "title": item["title"],
                            "path": item.get("path") or f"job-material://{record['material_id']}",
                            "snippet": item["snippet"],
                        }
                    )
            continue
        items.append(
            {
                "source_id": record["material_id"],
                "title": record["display_name"],
                "path": record.get("source_url") or f"job-material://{record['material_id']}",
                "snippet": record["text"][:8_000],
            }
        )
    return items


def pending_web_sources(records: list[dict]) -> list[dict]:
    return [
        record
        for record in records
        if record["kind"] == "web_source"
        and record.get("source") == "web"
        and record.get("status") == "pending"
    ]


def requested_action_type(user_request: str) -> str:
    lowered = user_request.lower()
    if "apply" in lowered:
        return "apply_job"
    if any(term in lowered for term in {"send", "email", "message the hiring manager"}):
        return "send_email"
    if "resume bullet" in lowered:
        return "resume_bullet"
    if "prep plan" in lowered or "interview plan" in lowered:
        return "prep_plan"
    return "outreach_draft"


def failure_location(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, TaskStateLoadError):
        return "labs.lab_03.src.state_store", "load_task_state"
    if isinstance(exc, NotImplementedError):
        if "persisted task state" in str(exc):
            return "labs.lab_03.src.state_store", "advance_task_state"
        return "labs.lab_03.src.tools", "search_sources"
    return "labs.lab_03.web_adapter", "chat"
