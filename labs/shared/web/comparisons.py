from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

from labs.shared.artifacts import (
    artifact_path,
    artifact_scope,
    read_json,
    task_state_path,
    write_json,
)
from labs.shared.config import ROOT_DIR, frozen_model_settings, load_settings
from labs.shared.web.contracts import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ComparisonRequest,
    ComparisonResponse,
    ComparisonRun,
    ErrorInfo,
    HarnessEvent,
    RunTrace,
    TraceLink,
    TraceParticipant,
    TraceSpan,
)
from labs.shared.web.errors import StageExecutionError
from labs.shared.web.execution_locks import workspace_execution_lock
from labs.shared.web.materials import MaterialStore
from labs.shared.web.registry import StageRegistration
from labs.shared.web.stage_executor import StageExecutor
from labs.shared.web.web_fetch import FetchedWebPage, fetch_web_page, frozen_web_pages


ROLE_NAMES = {"coordinator", "research", "summarize", "action"}
MAX_COMPARISON_ARTIFACT_SETS = 80
# This allowlist governs normalized trajectory spans only. Raw model-call events
# intentionally retain local Prompt & Model I/O for the dedicated UI evidence panel.
SAFE_DETAIL_KEYS = {
    "agent",
    "from",
    "to",
    "contract_fields",
    "source_ids",
    "source_count",
    "tool",
    "action_type",
    "status",
    "reason",
    "mode",
    "model_calls",
    "model",
    "schema",
    "input_fields",
    "output_fields",
    "profile_id",
    "job_description_id",
    "material_id",
    "material_ids",
    "budget",
    "limits",
    "backend",
    "demo_run_id",
    "state_id",
    "revision",
    "message_count",
}
SAFE_NESTED_DETAIL_KEYS = {
    "budget": {"turns", "tool_calls", "model_calls", "limits"},
    "limits": {"max_turns", "max_tool_calls", "max_model_calls", "max_sources"},
}


def run_comparison(
    *,
    registry: dict[str, StageRegistration],
    load_adapter: Callable[[str], type],
    materials: MaterialStore,
    request: ComparisonRequest,
    error_builder: Callable[[Exception], ErrorInfo],
) -> ComparisonResponse:
    with workspace_execution_lock(request.workspace_id):
        return _run_comparison(
            registry=registry,
            load_adapter=load_adapter,
            materials=materials,
            request=request,
            error_builder=error_builder,
        )


def _run_comparison(
    *,
    registry: dict[str, StageRegistration],
    load_adapter: Callable[[str], type],
    materials: MaterialStore,
    request: ComparisonRequest,
    error_builder: Callable[[Exception], ErrorInfo],
) -> ComparisonResponse:
    current = registry.get(request.current_stage)
    if current is None:
        raise ValueError("Unknown Lab stage.")
    if not current.public.available:
        raise ValueError("This Lab stage is not available yet.")
    previous_id = current.public.previous_stage
    if not previous_id:
        raise ValueError("Lab 1 has no previous stage to compare.")
    previous = registry.get(previous_id)
    if previous is None or not previous.public.available:
        raise ValueError("The previous Lab package is not installed.")
    cached_response = cached_comparison_response(request)
    if cached_response is not None:
        return cached_response

    comparison_id = f"comparison_{uuid.uuid4().hex}"
    snapshot_id = f"snapshot_{uuid.uuid4().hex}"
    current_prompt = latest_user_content(request.messages)
    before_workspace = comparison_side_workspace_id(
        request.workspace_id,
        request.session_id,
        request.current_stage,
        "before",
    )
    after_workspace = comparison_side_workspace_id(
        request.workspace_id,
        request.session_id,
        request.current_stage,
        "after",
    )
    side_state_references = {
        "before": managed_state_reference(previous_id, before_workspace),
        "after": managed_state_reference(request.current_stage, after_workspace),
    }
    records = copy.deepcopy(materials.context(request.workspace_id))
    frozen_sources = (
        freeze_pending_web_sources(records)
        if stage_number(request.current_stage) >= 3
        else {}
    )
    model_settings = load_settings()
    fingerprint = snapshot_fingerprint(
        records,
        request.messages,
        request.backend,
        model_settings.provider,
        model_settings.model,
        side_state_references=side_state_references,
    )
    snapshot_summary = {
        "snapshot_id": snapshot_id,
        "fingerprint": f"sha256:{fingerprint}",
        "backend": request.backend,
        "model_provider": model_settings.provider,
        "model": model_settings.model,
        "material_ids": [record["material_id"] for record in records],
        "material_kinds": sorted({record["kind"] for record in records}),
        "message_count": len(request.messages),
        "before_state_revision": (side_state_references["before"] or {}).get("revision", 0),
        "after_state_revision": (side_state_references["after"] or {}).get("revision", 0),
    }

    runtime_root = ROOT_DIR / ".runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix=f"{comparison_id}-", dir=runtime_root))
    try:
        isolated_store = MaterialStore(temp_root / "materials")
        isolated_store.seed_workspace(before_workspace, records)
        isolated_store.seed_workspace(after_workspace, records)

        before_root = ROOT_DIR / "artifacts" / "comparisons" / comparison_id / "before"
        after_root = ROOT_DIR / "artifacts" / "comparisons" / comparison_id / "after"
        with frozen_model_settings(model_settings), frozen_web_pages(frozen_sources):
            with artifact_scope(before_root):
                before = run_side(
                    stage_id=previous_id,
                    workspace_id=before_workspace,
                    request=request,
                    messages=request.messages,
                    snapshot_id=snapshot_id,
                    registry=registry,
                    load_adapter=load_adapter,
                    materials=isolated_store,
                    error_builder=error_builder,
                    artifact_root=before_root,
                )
            with artifact_scope(after_root):
                after = run_side(
                    stage_id=request.current_stage,
                    workspace_id=after_workspace,
                    request=request,
                    messages=request.messages,
                    snapshot_id=snapshot_id,
                    registry=registry,
                    load_adapter=load_adapter,
                    materials=isolated_store,
                    error_builder=error_builder,
                    artifact_root=after_root,
                )

        response = ComparisonResponse(
            comparison_id=comparison_id,
            input_snapshot=snapshot_summary,
            before=before,
            after=after,
            delta=build_delta(before.trace, after.trace),
            compare_note=compare_note(current, current_prompt),
        )
        manifest_path = artifact_path("comparisons", comparison_id, "comparison.json")
        manifest = response.model_dump()
        manifest["_owner"] = {
            "workspace_id": request.workspace_id,
            "session_id": request.session_id,
            "current_stage": request.current_stage,
        }
        write_json(manifest_path, manifest)
        cache_comparison_response(request, response)
        prune_comparison_artifacts(
            request.workspace_id,
            request.session_id,
            request.current_stage,
        )
        return response
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def comparison_request_fingerprint(request: ComparisonRequest) -> str:
    payload = request.model_dump(mode="json", exclude={"request_id"})
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def comparison_request_cache_directory(
    workspace_id: str,
    session_id: str | None = None,
    current_stage: str | None = None,
) -> Path:
    parts = ["comparison-requests", workspace_id]
    if session_id is not None:
        parts.append(session_id)
    if current_stage is not None:
        parts.append(current_stage)
    return ROOT_DIR / "artifacts" / Path(*parts)


def comparison_request_cache_file(
    workspace_id: str,
    session_id: str,
    current_stage: str,
    request_id: str,
) -> Path:
    return (
        comparison_request_cache_directory(
            workspace_id,
            session_id,
            current_stage,
        )
        / f"{request_id}.json"
    )


def comparison_request_cache_path(request: ComparisonRequest) -> Path:
    assert request.request_id is not None
    return comparison_request_cache_file(
        request.workspace_id,
        request.session_id,
        request.current_stage,
        request.request_id,
    )


def clear_comparison_request_cache(
    workspace_id: str,
    session_id: str | None = None,
    current_stage: str | None = None,
) -> None:
    shutil.rmtree(
        comparison_request_cache_directory(workspace_id, session_id, current_stage),
        ignore_errors=True,
    )


def owned_comparison_directories(
    workspace_id: str,
    session_id: str | None = None,
    current_stage: str | None = None,
) -> list[Path]:
    matches: list[Path] = []
    root = ROOT_DIR / "artifacts" / "comparisons"
    for manifest_path in root.glob("comparison_*/comparison.json"):
        try:
            owner = read_json(manifest_path).get("_owner", {})
        except (OSError, ValueError):
            continue
        if owner.get("workspace_id") != workspace_id:
            continue
        if session_id is not None and owner.get("session_id") != session_id:
            continue
        if current_stage is not None and owner.get("current_stage") != current_stage:
            continue
        matches.append(manifest_path.parent)
    return matches


def clear_comparison_artifacts(
    workspace_id: str,
    session_id: str | None = None,
    current_stage: str | None = None,
) -> None:
    for directory in owned_comparison_directories(
        workspace_id,
        session_id,
        current_stage,
    ):
        shutil.rmtree(directory, ignore_errors=True)


def prune_comparison_artifacts(
    workspace_id: str,
    session_id: str,
    current_stage: str,
) -> None:
    directories = sorted(
        owned_comparison_directories(workspace_id, session_id, current_stage),
        key=lambda directory: directory.stat().st_mtime_ns,
        reverse=True,
    )
    removed_ids = {
        directory.name
        for directory in directories[MAX_COMPARISON_ARTIFACT_SETS:]
    }
    for directory in directories[MAX_COMPARISON_ARTIFACT_SETS:]:
        shutil.rmtree(directory, ignore_errors=True)
    if not removed_ids:
        return
    cache_directory = comparison_request_cache_directory(
        workspace_id,
        session_id,
        current_stage,
    )
    for path in cache_directory.glob("*.json"):
        try:
            comparison_id = read_json(path).get("comparison_id")
        except (OSError, ValueError):
            continue
        if comparison_id in removed_ids:
            path.unlink(missing_ok=True)


def acknowledge_comparison_request(
    workspace_id: str,
    session_id: str,
    current_stage: str,
    request_id: str,
) -> None:
    comparison_request_cache_file(
        workspace_id,
        session_id,
        current_stage,
        request_id,
    ).unlink(missing_ok=True)


def cached_comparison_response(request: ComparisonRequest) -> ComparisonResponse | None:
    if request.request_id is None:
        return None
    cache_path = comparison_request_cache_path(request)
    if not cache_path.is_file():
        return None
    cached = read_json(cache_path)
    if cached.get("request_fingerprint") != comparison_request_fingerprint(request):
        raise ValueError("Comparison request id was already used for different input.")
    comparison_id = cached.get("comparison_id", "")
    if not re.fullmatch(r"comparison_[a-f0-9]{32}", comparison_id):
        raise ValueError("Cached comparison result is invalid.")
    manifest_path = ROOT_DIR / "artifacts" / "comparisons" / comparison_id / "comparison.json"
    if not manifest_path.is_file():
        raise ValueError("Cached comparison result is no longer available.")
    return ComparisonResponse.model_validate(read_json(manifest_path))


def cache_comparison_response(
    request: ComparisonRequest,
    response: ComparisonResponse,
) -> None:
    if request.request_id is None:
        return
    write_json(
        comparison_request_cache_path(request),
        {
            "request_fingerprint": comparison_request_fingerprint(request),
            "comparison_id": response.comparison_id,
        },
    )


def run_side(
    *,
    stage_id: str,
    workspace_id: str,
    request: ComparisonRequest,
    messages: list[ChatMessage],
    snapshot_id: str,
    registry: dict[str, StageRegistration],
    load_adapter: Callable[[str], type],
    materials: MaterialStore,
    error_builder: Callable[[Exception], ErrorInfo],
    artifact_root: Path,
) -> ComparisonRun:
    chat_request = ChatRequest(
        stage=stage_id,
        backend=request.backend,
        session_id=request.session_id,
        workspace_id=workspace_id,
        messages=messages,
    )
    try:
        response = StageExecutor(load_adapter).chat(
            registration=registry[stage_id],
            materials=materials,
            request=chat_request,
        )
        events = response.events
        artifacts = response.artifacts
        status = response.status
        error = response.error
    except StageExecutionError as exc:
        response = ChatResponse(
            status="error",
            stage=stage_id,
            run_id=exc.run_id,
            events=exc.events,
            artifacts=exc.artifacts,
            error=error_builder(exc.original),
        )
        events = exc.events
        artifacts = exc.artifacts
        status = "error"
        error = response.error
    except Exception as exc:
        failure_event = HarnessEvent(
            sequence=1,
            type="comparison",
            status="failed",
            component=f"labs.{stage_id}.comparison",
            operation="run_stage",
            summary=f"Comparison side failed with {type(exc).__name__}",
            details={"stage": stage_id},
        )
        response = ChatResponse(
            status="error",
            stage=stage_id,
            run_id=f"run_{uuid.uuid4().hex}",
            events=[failure_event],
            error=error_builder(exc),
        )
        events = [failure_event]
        artifacts = []
        status = "error"
        error = response.error

    trace = normalize_trace(stage_id, response.run_id, events)
    return ComparisonRun(
        stage=stage_id,
        run_id=response.run_id,
        input_snapshot_id=snapshot_id,
        status=status,
        assistant_message=response.assistant_message,
        events=events,
        trace=trace,
        state_summary=response.state_summary,
        artifacts=artifacts,
        error=error,
        artifact_root=artifact_root.relative_to(ROOT_DIR / "artifacts").as_posix(),
    )


def latest_user_content(messages: list[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.role == "user":
            return message.content
    raise ValueError("A comparison requires at least one user message.")


def freeze_pending_web_sources(records: list[dict[str, Any]]) -> dict[str, FetchedWebPage | Exception]:
    """Fetch queued web sources once so both comparison sides share one snapshot."""
    frozen: dict[str, FetchedWebPage | Exception] = {}
    for record in records:
        if record.get("kind") != "web_source" or record.get("status") != "pending":
            continue
        source_url = record["source_url"]
        try:
            page = fetch_web_page(source_url)
        except Exception as exc:
            frozen[source_url] = exc
            continue
        record.update(
            {
                "display_name": page.title,
                "text": page.text,
                "characters": len(page.text),
                "resolved_source_url": page.url,
            }
        )
        frozen[source_url] = page
        frozen[page.url] = page
    return frozen


def normalize_trace(stage_id: str, run_id: str, events: list[HarnessEvent]) -> RunTrace:
    participants: list[TraceParticipant] = []
    participant_ids: set[str] = set()
    spans: list[TraceSpan] = []
    links: list[TraceLink] = []
    pending_handoffs: list[dict[str, Any]] = []
    last_by_operation: dict[tuple[str, str], str] = {}
    last_tool_call_by_name: dict[tuple[str, str], str] = {}
    last_by_participant: dict[str, str] = {}
    offset = 0

    def ensure_participant(participant_id: str, kind: str, label: str, role: str | None = None) -> None:
        if participant_id in participant_ids:
            return
        participants.append(TraceParticipant(participant_id=participant_id, kind=kind, label=label, role=role))
        participant_ids.add(participant_id)

    for index, event in enumerate(events, start=1):
        details = event.details or {}
        participant_id, kind, label, role = participant_for(event, details)
        ensure_participant(participant_id, kind, label, role)
        duration = event.duration_ms
        start = details.get("start_offset_ms")
        start_offset = int(start) if isinstance(start, (int, float)) else offset
        if duration is not None:
            offset = max(offset, start_offset + max(0, duration))
        else:
            offset = max(offset, start_offset + 1)
        span_id = f"span_{index}_{safe_key(event.operation)}"
        parent_id = None
        operation_key = (participant_id, event.operation)
        if event.type in {"tool_result", "tool_output"}:
            tool_name = details.get("tool")
            if isinstance(tool_name, str):
                parent_id = last_tool_call_by_name.get((participant_id, tool_name))
            parent_id = parent_id or last_by_operation.get((participant_id, event.operation))
        elif event.type in {"guardrail", "approval_decision", "policy"}:
            parent_id = last_by_participant.get(participant_id)
        span = TraceSpan(
            span_id=span_id,
            parent_span_id=parent_id,
            participant_id=participant_id,
            semantic_key=semantic_key(event),
            kind=event.type,
            component=event.component,
            operation=event.operation,
            summary=event.summary,
            status=event.status,
            start_offset_ms=start_offset,
            duration_ms=duration,
            input_contract=contract_value(details, "input_contract"),
            output_contract=contract_value(details, "output_contract"),
            context_sources=context_sources(details),
            input_summary=safe_details(details, direction="input"),
            output_summary=safe_details(details, direction="output"),
            budget_delta=budget_delta(details),
        )
        spans.append(span)
        last_by_operation[operation_key] = span_id
        if event.type == "tool_call":
            tool_name = details.get("tool")
            if isinstance(tool_name, str):
                last_tool_call_by_name[(participant_id, tool_name)] = span_id
        last_by_participant[participant_id] = span_id

        for pending in pending_handoffs[:]:
            if pending["target_role"] != role or pending["source_span_id"] == span_id:
                continue
            links.append(
                TraceLink(
                    link_id=f"link_{len(links) + 1}",
                    kind="handoff",
                    source_span_id=pending["source_span_id"],
                    target_span_id=span_id,
                    contract_fields=pending["contract_fields"],
                    summary=pending["summary"],
                )
            )
            pending_handoffs.remove(pending)

        source_role = details.get("from")
        target_role = details.get("to")
        if isinstance(source_role, str) and isinstance(target_role, str):
            source_id = last_span_for_role(spans[:-1], source_role)
            if source_id is None and participant_id == source_role:
                source_id = span_id
            if source_id:
                contract_fields = [str(field) for field in details.get("contract_fields", [])]
                if event.type in {"delegation", "handoff"}:
                    pending_handoffs.append(
                        {
                            "source_span_id": source_id,
                            "handoff_span_id": span_id,
                            "target_role": target_role,
                            "contract_fields": contract_fields,
                            "summary": event.summary,
                        }
                    )
                elif source_id != span_id:
                    links.append(
                        TraceLink(
                            link_id=f"link_{len(links) + 1}",
                            kind="delegates",
                            source_span_id=source_id,
                            target_span_id=span_id,
                            contract_fields=contract_fields,
                            summary=event.summary,
                        )
                    )
        elif parent_id:
            links.append(
                TraceLink(
                    link_id=f"link_{len(links) + 1}",
                    kind="calls",
                    source_span_id=parent_id,
                    target_span_id=span_id,
                    summary=event.summary,
                )
            )

    for pending in pending_handoffs:
        links.append(
            TraceLink(
                link_id=f"link_{len(links) + 1}",
                kind="handoff",
                source_span_id=pending["source_span_id"],
                target_span_id=pending["handoff_span_id"],
                contract_fields=pending["contract_fields"],
                summary=pending["summary"],
            )
        )
    return RunTrace(trace_id=f"trace_{stage_id}_{run_id}", participants=participants, spans=spans, links=links)


def participant_for(event: HarnessEvent, details: dict[str, Any]) -> tuple[str, str, str, str | None]:
    role = details.get("agent")
    if event.type in {"delegation", "handoff"} and isinstance(details.get("from"), str):
        role = details["from"]
    component = event.component.lower()
    if not isinstance(role, str):
        role = next((candidate for candidate in ROLE_NAMES if candidate in component), None)
    if role in ROLE_NAMES:
        kind = "coordinator" if role == "coordinator" else "agent"
        return role, kind, role.title(), role
    if "runtime" in component or "openclaw" in component:
        return "runtime", "runtime", "Runtime", None
    return "workflow", "workflow", "Workflow", None


def semantic_key(event: HarnessEvent) -> str:
    explicit_key = event.details.get("semantic_key")
    if (
        isinstance(explicit_key, str)
        and len(explicit_key) <= 120
        and re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", explicit_key)
    ):
        return explicit_key
    operation = event.operation.lower()
    stable_operations = {
        "complete": "model.generate",
        "generate_report_json": "model.generate_report",
        "validate_fit_gap_report": "schema.validate_fit_gap",
        "mark_report_generated": "state.persist_job_prep",
        "load_task_state": "state.load_task",
        "advance_task_state": "state.advance_task",
        "fetch_web_page": "tool.fetch_web_page",
        "complete_web_source": "tool.store_run_local_source",
        "request_tool_call": "model.select_tool",
        "search_sources": "tool.search_sources",
        "draft_outreach": "model.draft_outreach",
        "draft_action": "policy.classify_action",
        "classify_action": "policy.classify_action",
        "load_skill": "skill.load_job_prep",
        "generate_claims": "model.generate_claims",
        "build_evidence_notes": "evidence.verify_claims",
        "ping": "capability.mcp_boundary",
        "prepare_eval_target": "eval.prepare_target",
        "record_run_trace": "trace.record_run",
        "run_eval": "eval.run_suite",
        "run_workflow": "agent.run_workflow",
        "build_application_workspace": "artifact.build_workspace",
    }
    if operation in stable_operations:
        return stable_operations[operation]
    component = re.sub(r"[^a-z0-9]+", ".", event.component.lower()).strip(".")
    normalized_operation = re.sub(r"[^a-z0-9]+", ".", operation).strip(".")
    return f"{event.type}.{component}.{normalized_operation}"


def last_span_for_role(spans: list[TraceSpan], role: str) -> str | None:
    for span in reversed(spans):
        if span.participant_id == role:
            return span.span_id
    return None


def safe_details(details: dict[str, Any], *, direction: str) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for key, value in details.items():
        if key not in SAFE_DETAIL_KEYS:
            continue
        if direction == "input" and key in {"status", "reason", "source_count", "source_ids", "model_calls"}:
            continue
        if direction == "output" and key in {"query", "input", "requested_url", "profile_id", "job_description_id"}:
            continue
        selected[key] = clip_value(value, parent_key=key)
    return selected


def contract_value(details: dict[str, Any], key: str) -> str | None:
    value = details.get(key)
    return value[:160] if isinstance(value, str) else None


def context_sources(details: dict[str, Any]) -> list[str]:
    value = details.get("context_sources")
    if not isinstance(value, list):
        return []
    return [str(item)[:120] for item in value[:12]]


def clip_value(value: Any, *, parent_key: str | None = None) -> Any:
    if isinstance(value, dict):
        allowed_keys = SAFE_NESTED_DETAIL_KEYS.get(parent_key, set())
        return {
            str(key): clip_value(item, parent_key=str(key))
            for key, item in list(value.items())[:12]
            if str(key) in allowed_keys
        }
    if isinstance(value, list):
        return [clip_value(item, parent_key=parent_key) for item in value[:12]]
    if isinstance(value, str):
        return value[:240]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:240]


def budget_delta(details: dict[str, Any]) -> dict[str, int]:
    budget = details.get("budget")
    if not isinstance(budget, dict):
        return {}
    return {key: int(value) for key, value in budget.items() if key in {"turns", "tool_calls", "model_calls"} and isinstance(value, (int, float))}


def occurrence_keys(spans: list[TraceSpan]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    keys: list[tuple[str, int]] = []
    for span in spans:
        occurrence = counts.get(span.semantic_key, 0)
        keys.append((span.semantic_key, occurrence))
        counts[span.semantic_key] = occurrence + 1
    return keys


def build_delta(before: RunTrace, after: RunTrace) -> dict[str, Any]:
    before_keys = occurrence_keys(before.spans)
    after_keys = occurrence_keys(after.spans)
    ownership: list[dict[str, str]] = []
    changed: list[dict[str, Any]] = []
    input_contract_changes: list[dict[str, Any]] = []
    output_contract_changes: list[dict[str, Any]] = []
    context_changes: list[dict[str, Any]] = []
    before_by_key = {key: span for key, span in zip(before_keys, before.spans)}
    for key, span in zip(after_keys, after.spans):
        previous = before_by_key.get(key)
        if not previous:
            continue
        if previous.participant_id != span.participant_id:
            ownership.append({"semantic_key": span.semantic_key, "before": previous.participant_id, "after": span.participant_id})
        if previous.input_contract != span.input_contract:
            input_contract_changes.append(
                {"semantic_key": span.semantic_key, "before": previous.input_contract, "after": span.input_contract}
            )
        if previous.output_contract != span.output_contract:
            output_contract_changes.append(
                {"semantic_key": span.semantic_key, "before": previous.output_contract, "after": span.output_contract}
            )
        if previous.context_sources != span.context_sources:
            context_changes.append(
                {"semantic_key": span.semantic_key, "before": previous.context_sources, "after": span.context_sources}
            )
        if (
            previous.kind != span.kind
            or previous.component != span.component
            or previous.operation != span.operation
            or previous.status != span.status
            or previous.parent_span_id != span.parent_span_id
        ):
            changed.append(
                {
                    "semantic_key": span.semantic_key,
                    "before": previous.model_dump(),
                    "after": span.model_dump(),
                }
            )

    before_span_keys = {span.span_id: span.semantic_key for span in before.spans}
    after_span_keys = {span.span_id: span.semantic_key for span in after.spans}
    before_link_keys = {link_key(before_span_keys, link) for link in before.links}
    after_link_keys = {link_key(after_span_keys, link) for link in after.links}
    added_span_keys = [
        {"semantic_key": semantic_key, "occurrence": occurrence}
        for semantic_key, occurrence in after_keys
        if (semantic_key, occurrence) not in before_keys
    ]
    removed_span_keys = [
        {"semantic_key": semantic_key, "occurrence": occurrence}
        for semantic_key, occurrence in before_keys
        if (semantic_key, occurrence) not in after_keys
    ]
    return {
        "added_participants": [participant.model_dump() for participant in after.participants if participant.participant_id not in {item.participant_id for item in before.participants}],
        "removed_participants": [participant.model_dump() for participant in before.participants if participant.participant_id not in {item.participant_id for item in after.participants}],
        "added_spans": [span.model_dump() for key, span in zip(after_keys, after.spans) if key not in before_keys],
        "removed_spans": [span.model_dump() for key, span in zip(before_keys, before.spans) if key not in after_keys],
        "added_span_keys": added_span_keys,
        "removed_span_keys": removed_span_keys,
        "changed_spans": changed,
        "ownership_changes": ownership,
        "added_links": [link.model_dump() for link in after.links if link_key(after_span_keys, link) not in before_link_keys],
        "removed_links": [link.model_dump() for link in before.links if link_key(before_span_keys, link) not in after_link_keys],
        "input_contract_changes": input_contract_changes,
        "output_contract_changes": output_contract_changes,
        "context_changes": context_changes,
    }


def link_key(span_keys: dict[str, str], link: TraceLink) -> tuple[str, str, str, tuple[str, ...]]:
    return (
        link.kind,
        span_keys.get(link.source_span_id, ""),
        span_keys.get(link.target_span_id, ""),
        tuple(link.contract_fields),
    )


def compare_note(registration: StageRegistration, prompt: str) -> str:
    for example in registration.public.examples:
        if example.prompt.strip() == prompt.strip():
            return example.compare_note
    return f"Compared the same prompt across {registration.public.previous_stage} and {registration.public.id}."


def snapshot_fingerprint(
    records: list[dict[str, Any]],
    messages: list[Any],
    backend: str,
    model_provider: str,
    model: str,
    *,
    side_state_references: dict[str, dict[str, Any] | None] | None = None,
) -> str:
    material_data = []
    for record in sorted(records, key=lambda item: item["material_id"]):
        material_data.append(
            {
                "material_id": record["material_id"],
                "kind": record["kind"],
                "display_name": record.get("display_name"),
                "content_hash": hashlib.sha256(record.get("text", "").encode("utf-8")).hexdigest(),
                "source_url": record.get("source_url"),
                "resolved_source_url": record.get("resolved_source_url"),
                "status": record.get("status"),
            }
        )
    payload = {
        "backend": backend,
        "model_provider": model_provider,
        "model": model,
        "materials": material_data,
        "messages": [message.model_dump() for message in messages],
        "side_state_references": side_state_references or {},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def comparison_workspace_prefix(workspace_id: str) -> str:
    owner_key = hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()[:12]
    return f"cmp_{owner_key}_"


def comparison_side_workspace_id(
    workspace_id: str,
    session_id: str,
    current_stage: str,
    side: str,
) -> str:
    if side not in {"before", "after"}:
        raise ValueError("Comparison side must be before or after.")
    session_key = hashlib.sha256(
        f"{workspace_id}:{session_id}:{current_stage}".encode("utf-8")
    ).hexdigest()[:16]
    return f"{comparison_workspace_prefix(workspace_id)}{session_key}_{side}"


def managed_state_reference(stage_id: str, workspace_id: str) -> dict[str, Any] | None:
    path = task_state_path(stage_id, workspace_id)
    if not path.is_file():
        return None
    try:
        state = read_json(path)
    except (OSError, ValueError):
        return None
    if (
        not isinstance(state, dict)
        or not isinstance(state.get("state_id"), str)
        or not isinstance(state.get("revision"), int)
    ):
        return None
    return {"state_id": state["state_id"], "revision": state["revision"]}


def safe_key(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value)[:48]


def stage_number(stage_id: str) -> int:
    match = re.fullmatch(r"lab_(\d+)", stage_id)
    if match is None:
        raise ValueError(f"Invalid Lab stage id: {stage_id}")
    return int(match.group(1))
