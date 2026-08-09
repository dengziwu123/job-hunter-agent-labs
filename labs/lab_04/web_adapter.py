from __future__ import annotations

import uuid
from time import perf_counter

from labs.lab_03.web_adapter import build_tool_action_capability
from labs.lab_04.src.mcp_client_adapter import MCPBoundaryError
from labs.lab_04.src.skill_loader import LoadedSkill, load_skill
from labs.shared.artifacts import artifact_path, relative_artifact_path, write_json
from labs.shared.web.contracts import ArtifactLink, ChatRequest, ChatResponse, HarnessEvent
from labs.shared.web.errors import StageExecutionError
from labs.shared.web.materials import MaterialStore
from labs.shared.web.tracing import write_run_trace


# Normal fixtures fit without trimming. This generous safety bound only becomes
# active when real uploaded/fetched inputs make the rendered model prompt large.
UI_CONTEXT_BUDGET_TOKENS = 4_000
MATERIAL_EVIDENCE_KINDS = {"candidate_profile", "job_description"}
MAX_MATERIAL_EVIDENCE_CHARACTERS = 8_000

FAILURE_EVENT_TYPES = {
    "load_skill": "skill",
    "load_task_prompt": "prompt",
    "select_context": "context_budget",
    "build_evidence_notes": "evidence",
}


def record_mcp_protocol_operations(
    events: list[HarnessEvent],
    protocol_operations: list[dict],
) -> None:
    for protocol_operation in protocol_operations:
        events.append(
            HarnessEvent(
                sequence=len(events) + 1,
                type=protocol_operation.get("event_type", "capability_boundary"),
                status="completed",
                component=protocol_operation.get(
                    "component", "labs.lab_04.src.mcp_client_adapter.Client"
                ),
                operation=protocol_operation["operation"],
                summary=protocol_operation["summary"],
                duration_ms=protocol_operation["duration_ms"],
                details=protocol_operation["details"],
            )
        )


class Lab04Adapter:
    stage_id = "lab_04"

    def __init__(self, materials: MaterialStore) -> None:
        self.materials = materials

    def chat(self, request: ChatRequest) -> ChatResponse:
        run_id = f"run_{uuid.uuid4().hex}"
        events: list[HarnessEvent] = []
        artifacts: list[ArtifactLink] = []

        try:
            payload = build_evidence_capability(
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
                    type=(
                        "capability_boundary"
                        if isinstance(exc, MCPBoundaryError)
                        else FAILURE_EVENT_TYPES.get(operation, "evidence")
                    ),
                    status="failed",
                    component=component,
                    operation=operation,
                    summary=f"Lab 4 complete run failed with {type(exc).__name__}",
                    details={"stage": self.stage_id},
                )
            )
            trace = write_run_trace(self.stage_id, run_id, events)
            raise StageExecutionError(exc, events, [*artifacts, trace], run_id) from exc

        trace = write_run_trace(self.stage_id, run_id, events)
        return ChatResponse(
            status="ok",
            stage=self.stage_id,
            run_id=run_id,
            assistant_message=format_evidence_response(payload["claims"]),
            events=events,
            state_summary={
                "structured_report": payload["structured_report"],
                "task_state": payload["task_state"],
                "claims": payload["claims"],
                "context_budget": payload["context_budget"],
                "model": payload["model"],
                "skill": payload["skill"],
                "task_prompt": payload["task_prompt"],
                "mcp_boundary": payload["mcp_boundary"],
                "limitations": ["No eval suite proves behavior across cases yet"],
            },
            artifacts=[*artifacts, trace],
        )

    def run_eval(self, _: str) -> None:
        raise NotImplementedError("Eval is introduced in Lab 5.")


def build_evidence_capability(
    *,
    materials: MaterialStore,
    request: ChatRequest,
    run_id: str,
    stage_id: str,
    events: list[HarnessEvent],
    artifacts: list[ArtifactLink],
) -> dict:
    """Compose Lab 1-4 primitives inside one stage run."""
    from labs.lab_04.src.claim_generation import (
        ClaimGenerationModel,
        claim_provider_input_tokens,
        claim_prompt_protected_tokens,
    )
    from labs.lab_04.src.context_budget import render_evidence_sources, select_context
    from labs.lab_04.src.evidence import build_evidence_notes
    from labs.lab_04.src.job_research import research_job_board
    from labs.lab_04.src.prompt_loader import load_task_prompt
    from labs.lab_04.src.retrieval import SourceRecord
    from labs.lab_04.src.skill_loader import skill_prompt_block

    tool_action = build_tool_action_capability(
        materials=materials,
        request=request,
        run_id=run_id,
        stage_id=stage_id,
        events=events,
        artifacts=artifacts,
    )
    material_sources = [
        SourceRecord(
            source_id=record["material_id"],
            title=record["display_name"],
            path=f"job-material://{record['material_id']}",
            snippet=record["text"][:MAX_MATERIAL_EVIDENCE_CHARACTERS],
        )
        for record in materials.context(request.workspace_id)
        if record.get("kind") in MATERIAL_EVIDENCE_KINDS
        and record.get("status") == "ready"
        and record.get("text")
    ]
    sources = [
        *material_sources,
        *[SourceRecord.model_validate(item) for item in tool_action["sources"]],
    ]

    started = perf_counter()
    skill = load_student_skill()
    skill_duration_ms = max(1, int((perf_counter() - started) * 1000))
    events.append(
        HarnessEvent(
            sequence=len(events) + 1,
            type="skill",
            status="completed",
            component="labs.lab_04.skills.job-prep.SKILL.md",
            operation="load_skill",
            summary="Loaded truthfulness, evidence, and draft-only rules into the current run",
            duration_ms=skill_duration_ms,
            details=skill.model_dump(),
        )
    )

    # Task instructions, Skill rules and Lab 3's bounded conversation
    # context are protected content; only the evidence competes for room.
    skill_prompt = skill_prompt_block(skill)
    started = perf_counter()
    task_prompt = load_task_prompt()
    prompt_duration_ms = max(1, int((perf_counter() - started) * 1000))
    events.append(
        HarnessEvent(
            sequence=len(events) + 1,
            type="prompt",
            status="completed",
            component="labs.lab_04.prompts.grounded-job-research.md",
            operation="load_task_prompt",
            summary="Loaded the student-authored grounded job-research task prompt",
            duration_ms=prompt_duration_ms,
            details={
                "semantic_key": "prompt.load_task_template",
                **task_prompt.model_dump(exclude={"template"}),
                "template": task_prompt.template,
            },
        )
    )

    latest_request = request.messages[-1].content
    tool_decision_request = tool_action.get("request_context") or latest_request
    try:
        mcp_boundary = research_job_board(
            task_prompt=task_prompt,
            skill_rules=skill_prompt,
            user_request=tool_decision_request,
            evidence_sources=render_evidence_sources(sources),
        )
    except MCPBoundaryError as exc:
        record_mcp_protocol_operations(events, exc.completed_operations)
        raise
    mcp_protocol_operations = mcp_boundary.pop("protocol_operations")
    record_mcp_protocol_operations(events, mcp_protocol_operations)

    job_openings = mcp_boundary["records"]
    job_sources = [
        SourceRecord(
            source_id=f"job-{record['ats']}-{record['job_id']}",
            title=record["title"],
            path=record["url"],
            snippet=". ".join(
                value
                for value in (
                    record["title"],
                    record.get("location", ""),
                    record.get("department", ""),
                    record.get("summary", ""),
                )
                if value
            ),
        )
        for record in job_openings
    ]
    sources = [*job_sources, *sources]
    available_source_count = len(sources)
    available_tools = mcp_boundary["tool_descriptors"]
    # Full opening records become ordinary evidence so keep/truncate/drop applies
    # to them exactly once. The second prompt section contains references only;
    # otherwise an opening dropped here would still reach the model untrimmed.
    job_opening_refs = [{"source_id": source.source_id} for source in job_sources]
    # Current Lab 4 packages also carry the upgraded Lab 3 adapter. This remains
    # defensive for workspaces assembled outside that supported package path.
    request_context = tool_action.get("request_context", "")
    claim_model = ClaimGenerationModel()
    claim_settings = getattr(claim_model, "settings", None)

    def opening_refs_for(candidate_sources: list[SourceRecord]) -> list[dict]:
        candidate_ids = {source.source_id for source in candidate_sources}
        return [
            reference
            for reference in job_opening_refs
            if reference["source_id"] in candidate_ids
        ]

    # Measured the same way the reservation measures it, so the Inspector's
    # three parts add back up to protected_tokens.
    request_context_tokens = claim_prompt_protected_tokens(
        skill_prompt,
        request_context,
        task_prompt,
        available_tools,
        [],
        settings=claim_settings,
    ) - claim_prompt_protected_tokens(
        skill_prompt,
        "",
        task_prompt,
        available_tools,
        [],
        settings=claim_settings,
    )
    protected_tokens = claim_prompt_protected_tokens(
        skill_prompt,
        request_context,
        task_prompt,
        available_tools,
        [],
        settings=claim_settings,
    )
    started = perf_counter()
    selected = select_context(
        sources,
        budget_tokens=UI_CONTEXT_BUDGET_TOKENS,
        protected_tokens=protected_tokens,
        input_token_counter=lambda candidate_sources: claim_provider_input_tokens(
            candidate_sources,
            skill_prompt,
            request_context,
            task_prompt,
            available_tools,
            opening_refs_for(candidate_sources),
            settings=claim_settings,
        ),
    )
    budget_duration_ms = max(1, int((perf_counter() - started) * 1000))
    sources = selected.sources
    selected_source_ids = {source.source_id for source in sources}
    selected_job_opening_refs = [
        reference
        for reference in job_opening_refs
        if reference["source_id"] in selected_source_ids
    ]
    events.append(
        HarnessEvent(
            sequence=len(events) + 1,
            type="context_budget",
            status="completed",
            component="labs.lab_04.src.context_budget",
            operation="select_context",
            summary=(
                f"Fit {len(selected.sources)} of "
                f"{available_source_count} sources into "
                f"{selected.budget_tokens} tokens "
                f"({selected.protected_tokens} reserved for prompt instructions, "
                f"Skill rules and the current request)"
            ),
            duration_ms=budget_duration_ms,
            details={
                **selected.model_dump(exclude={"sources"}),
                "skill_tokens": skill.estimated_tokens,
                "request_context_tokens": request_context_tokens,
                "prompt_scaffold_tokens": selected.protected_tokens
                - skill.estimated_tokens
                - request_context_tokens,
            },
        )
    )

    started = perf_counter()
    claims = claim_model.generate_claims(
        sources,
        skill_prompt=skill_prompt,
        request_context=request_context,
        task_prompt=task_prompt,
        available_tools=available_tools,
        job_openings=selected_job_opening_refs,
    )
    claims_duration_ms = max(1, int((perf_counter() - started) * 1000))
    events.append(
        HarnessEvent(
            sequence=len(events) + 1,
            type="model_call",
            status="completed",
            component="labs.lab_04.src.claim_generation.ClaimGenerationModel",
            operation="generate_claims",
            summary=f"Generated claims from retrieved snippets using {claim_model.claim_source}",
            duration_ms=claims_duration_ms,
            details={
                "mode": claim_model.mode,
                "claim_source": claim_model.claim_source,
                "model_calls": claim_model.calls,
                "claim_count": len(claims),
                "model_io": claim_model.last_io,
            },
        )
    )

    started = perf_counter()
    notes = build_evidence_notes(claims, sources)
    evidence_duration_ms = max(1, int((perf_counter() - started) * 1000))
    supported = sum(note.status == "supported" for note in notes)
    unsupported = sum(note.status == "unsupported" for note in notes)
    events.append(
        HarnessEvent(
            sequence=len(events) + 1,
            type="evidence",
            status="completed",
            component="labs.lab_04.src.evidence",
            operation="build_evidence_notes",
            summary=(
                f"Evidence review found {supported} verified claims; "
                f"{unsupported} need stronger evidence"
            ),
            duration_ms=evidence_duration_ms,
            details={
                "claim_count": len(notes),
                "source_ids": [source.source_id for source in sources],
                "supported": supported,
                "unsupported": unsupported,
            },
        )
    )

    output_path = artifact_path(stage_id, "runs", run_id, "evidence_report.json")
    payload = {
        "run_id": run_id,
        "stage": stage_id,
        "material_ids": tool_action["material_ids"],
        "profile": tool_action["profile"],
        "job_description": tool_action["job_description"],
        "structured_report": tool_action["structured_report"],
        "task_state": tool_action["task_state"],
        "sources": [source.model_dump() for source in sources],
        "claims": [note.model_dump() for note in notes],
        "context_budget": selected.model_dump(exclude={"sources"}),
        "model": {
            "mode": claim_model.mode,
            "claim_source": claim_model.claim_source,
            "model_calls": claim_model.calls,
            "model_io": claim_model.last_io,
        },
        "skill": skill.model_dump(),
        "task_prompt": task_prompt.model_dump(exclude={"template"}),
        "mcp_boundary": mcp_boundary,
        "draft_action": tool_action["action"],
    }
    write_json(output_path, payload)
    artifacts.append(
        ArtifactLink(
            label="Evidence report",
            path=relative_artifact_path(output_path),
        )
    )
    return payload


def format_evidence_response(notes: list[dict]) -> str:
    return "\n".join(note["claim"] for note in notes)


def load_student_skill() -> LoadedSkill:
    return load_skill(use_skill=True)


def failure_location(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, MCPBoundaryError):
        return exc.component, exc.operation
    message = str(exc).lower()
    if "skill" in message:
        return "labs.lab_04.skills.job-prep.SKILL.md", "load_skill"
    if "prompt" in message:
        return "labs.lab_04.prompts.grounded-job-research.md", "load_task_prompt"
    if "context" in message or "budget" in message:
        return "labs.lab_04.src.context_budget", "select_context"
    return "labs.lab_04.src.evidence", "build_evidence_notes"
