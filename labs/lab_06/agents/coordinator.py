from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from labs.shared.artifacts import artifact_path
from labs.lab_02.src.schemas import FitGapReport
from labs.lab_04.src.context_budget import estimate_tokens, select_context
from labs.lab_04.src.retrieval import SourceRecord
from labs.lab_04.src.skill_loader import load_skill, skill_prompt_block
from labs.lab_06 import model
from labs.lab_06.agents import action_agent, research_agent, summarize_agent
from labs.lab_06.config import load_config
from labs.lab_06.contracts import (
    ContractPayloadError,
    ContractPayloadValidation,
    validate_contract,
    validate_contract_payload,
    validate_instruction,
)
from labs.lab_06.errors import BudgetExceeded, ContextBudgetExceeded, WorkflowStopError
from labs.lab_06.guardrails import require_approval


AGENTS = {
    "research": research_agent,
    "summarize": summarize_agent,
    "action": action_agent,
}
_active_trace_path: ContextVar[Path | None] = ContextVar("lab_06_trace_path", default=None)

# Keep the same context limit used by the Lab 4 web path. Fixed instructions,
# Skill rules and the bounded request reserve their space first; research
# evidence competes for the remainder.
CONTEXT_BUDGET_TOKENS = 4_000


@dataclass
class BudgetState:
    orchestration_events: int = 0
    tool_calls: int = 0
    model_calls: int = 0


def write_trace_event(event: dict[str, Any]) -> None:
    path = _active_trace_path.get() or artifact_path("lab_06", "multi_agent_trace.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def charge_budget(event: dict[str, Any], budget: BudgetState) -> None:
    event_type = event.get("event")
    if event_type in {"delegation", "handoff", "action_draft"}:
        budget.orchestration_events += 1
    if event_type == "tool_call":
        budget.tool_calls += 1
    budget.model_calls = model.calls_used()


def write_budgeted_trace_event(event: dict[str, Any], budget: BudgetState, config: Any) -> None:
    charge_budget(event, budget)
    if budget.orchestration_events > config.max_turns:
        raise BudgetExceeded(
            "Lab 6 max_turns budget exceeded.",
            stop_reason="orchestration_event_budget_exceeded",
        )
    if budget.tool_calls > config.max_tool_calls:
        raise BudgetExceeded(
            "Lab 6 max_tool_calls budget exceeded.",
            stop_reason="tool_call_budget_exceeded",
        )
    if budget.model_calls > config.max_model_calls:
        raise BudgetExceeded(
            "Lab 6 max_model_calls budget exceeded.",
            stop_reason="model_call_budget_exceeded",
        )
    write_trace_event(event)


def budget_snapshot(
    budget: BudgetState,
    config: Any | None = None,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        # Keep the public field for compatibility while naming what it counts.
        "turns": budget.orchestration_events,
        "orchestration_events": budget.orchestration_events,
        "turn_budget_unit": "orchestration_event",
        "tool_calls": budget.tool_calls,
        "model_calls": budget.model_calls,
        "budget_scope": "lab_06_coordinator_and_role_owned_operations",
        "excluded_operations": [
            "inherited_lab_02_model_calls",
            "inherited_lab_04_model_calls",
            "inherited_mcp_protocol_operations",
        ],
    }
    if config is not None:
        snapshot["limits"] = {
            "max_turns": config.max_turns,
            "max_tool_calls": config.max_tool_calls,
            "max_model_calls": config.max_model_calls,
        }
    return snapshot


def validate_agent_payload(
    *,
    role: str,
    payload: Any,
    direction: str,
) -> None:
    module = AGENTS[role]

    try:
        validation = validate_contract_payload(
            module.CONTRACT,
            payload,
            direction=direction,
        )
    except ContractPayloadError as exc:
        write_trace_event(
            {
                **payload_validation_trace_event(role, exc.validation),
                "status": "failed",
                "error_type": type(exc).__name__,
            }
        )
        raise
    write_trace_event(
        {**payload_validation_trace_event(role, validation), "status": "completed"}
    )


def payload_validation_trace_event(
    role: str,
    validation: ContractPayloadValidation,
) -> dict[str, Any]:
    return {
        "event": "contract_validation",
        "agent": role,
        "component": f"labs.lab_06.agents.{role}_agent",
        "operation": f"validate_{validation.direction}",
        "direction": validation.direction,
        "required_fields": validation.required_fields,
        "provided_fields": validation.provided_fields,
        "missing_fields": validation.missing_fields,
    }


def failure_stop_reason(exc: Exception) -> str:
    if isinstance(exc, WorkflowStopError):
        return exc.stop_reason
    return "workflow_failed"


def validate_agents() -> None:
    for module in AGENTS.values():
        validate_instruction(module.INSTRUCTION)
        validate_contract(module.CONTRACT)


def run_workflow(
    *,
    profile: dict,
    job_description: dict,
    source_items: list[dict] | None,
    prior_report: dict,
    requested_action: str,
    user_request: str,
    skill_prompt: str,
    trace_path: Path | None = None,
    on_planned_query: Callable[[str], list[dict[str, Any]] | None] | None = None,
) -> dict[str, Any]:
    config = load_config()
    active_trace_path = trace_path or artifact_path("lab_06", "multi_agent_trace.jsonl")
    if active_trace_path.exists():
        active_trace_path.unlink()
    model.reset_session(max_model_calls=config.max_model_calls)
    budget = BudgetState()
    trace_token = _active_trace_path.set(active_trace_path)
    model_trace_token = model.set_trace_writer(
        lambda event: write_budgeted_trace_event(event, budget, config)
    )

    try:
        validate_agents()
        if config.max_turns <= 0 or config.max_tool_calls <= 0 or config.max_model_calls <= 0:
            raise WorkflowStopError(
                "Lab 6 config limits must be positive.",
                stop_reason="invalid_budget_config",
            )

        research_input = {
            "profile_id": profile["id"],
            "job_description_id": job_description["id"],
            "query": user_request,
            "limit": 3,
            "source_items": source_items,
        }
        write_budgeted_trace_event(
            {
                "event": "delegation",
                "from": "coordinator",
                "to": "research",
                "component": "labs.lab_06.agents.research_agent",
                "operation": "run",
                "profile_id": profile["id"],
                "job_description_id": job_description["id"],
            },
            budget,
            config,
        )
        planned_query = research_agent.plan_query(research_input)
        if not planned_query:
            raise WorkflowStopError(
                "Research agent returned an empty search query.",
                stop_reason="empty_research_query",
            )
        research_run_input = {**research_input, "search_query": planned_query}
        validate_agent_payload(
            role="research",
            payload=research_run_input,
            direction="input",
        )
        boundary_sources = (
            on_planned_query(planned_query) or []
            if on_planned_query is not None
            else []
        )
        write_budgeted_trace_event(
            {
                "event": "tool_call",
                "agent": "research",
                "component": "labs.lab_03.src.tools",
                "operation": "search_sources",
                "input": {"query": planned_query, "limit": 3},
            },
            budget,
            config,
        )
        research_output = research_agent.run(research_run_input)
        source_ids = (
            research_output.get("source_ids")
            if isinstance(research_output, Mapping)
            else None
        )
        source_ids_are_known = isinstance(source_ids, list)
        write_budgeted_trace_event(
            {
                "event": "tool_result",
                "agent": "research",
                "component": "labs.lab_03.src.tools",
                "operation": "search_sources",
                "status": "completed" if source_ids_are_known else "unknown",
                "source_count": len(source_ids) if source_ids_are_known else None,
                "source_ids": source_ids if source_ids_are_known else None,
            },
            budget,
            config,
        )
        validate_agent_payload(
            role="research",
            payload=research_output,
            direction="output",
        )
        local_source_records = research_output.get("sources") or [
            {
                "source_id": source_id,
                "title": source_id,
                "path": f"source://{source_id}",
                "snippet": snippet,
            }
            for source_id, snippet in zip(
                research_output["source_ids"],
                research_output["source_snippets"],
                strict=False,
            )
        ]

        source_records_by_id: dict[str, dict[str, Any]] = {}
        for source in [*boundary_sources, *local_source_records]:
            source_records_by_id.setdefault(source["source_id"], source)
        source_records = list(source_records_by_id.values())
        research_output = {
            **research_output,
            "sources": source_records,
            "source_ids": [source["source_id"] for source in source_records],
            "source_snippets": [source["snippet"] for source in source_records],
        }

        retrieved_sources = [
            SourceRecord.model_validate(source) for source in source_records
        ]
        candidate_constraints = ["Do not invent experience."]
        skill_tokens = estimate_tokens(skill_prompt)
        protected_tokens = summarize_agent.summarize_prompt_protected_tokens(
            skill_prompt,
            candidate_constraints,
            user_request,
        )
        request_context_tokens = (
            protected_tokens
            - summarize_agent.summarize_prompt_protected_tokens(
                skill_prompt,
                candidate_constraints,
                "",
            )
        )
        started = perf_counter()
        try:
            selected = select_context(
                retrieved_sources,
                budget_tokens=CONTEXT_BUDGET_TOKENS,
                protected_tokens=protected_tokens,
            )
        except ValueError as exc:
            write_trace_event(
                {
                    "event": "context_budget",
                    "agent": "research",
                    "component": "labs.lab_04.src.context_budget",
                    "operation": "select_context",
                    "semantic_key": "context.select_budgeted",
                    "status": "failed",
                    "duration_ms": max(1, int((perf_counter() - started) * 1000)),
                    "source_count_before": len(retrieved_sources),
                    "source_count_after": 0,
                    "skill_tokens": skill_tokens,
                    "request_context_tokens": request_context_tokens,
                    "prompt_scaffold_tokens": protected_tokens
                    - skill_tokens
                    - request_context_tokens,
                    "budget_tokens": CONTEXT_BUDGET_TOKENS,
                    "protected_tokens": protected_tokens,
                    "estimated_tokens": protected_tokens,
                    "remaining_tokens": CONTEXT_BUDGET_TOKENS - protected_tokens,
                }
            )
            raise ContextBudgetExceeded(str(exc)) from exc
        context_budget = selected.model_dump(exclude={"sources"})
        selected_source_ids = [source.source_id for source in selected.sources]
        context_budget["selected_source_ids"] = selected_source_ids
        write_trace_event(
            {
                "event": "context_budget",
                "agent": "research",
                "component": "labs.lab_04.src.context_budget",
                "operation": "select_context",
                "semantic_key": "context.select_budgeted",
                "status": "completed",
                "duration_ms": max(1, int((perf_counter() - started) * 1000)),
                "source_count_before": len(retrieved_sources),
                "source_count_after": len(selected.sources),
                "skill_tokens": skill_tokens,
                "request_context_tokens": request_context_tokens,
                "prompt_scaffold_tokens": selected.protected_tokens
                - skill_tokens
                - request_context_tokens,
                **context_budget,
            }
        )
        source_records = [source.model_dump() for source in selected.sources]
        source_snippets = [source.snippet for source in selected.sources]

        summary_input = {
            "sources": source_records,
            "source_ids": selected_source_ids,
            "source_snippets": source_snippets,
            "prior_report": prior_report,
            "candidate_constraints": candidate_constraints,
            "user_request": user_request,
            "skill_prompt": skill_prompt,
        }
        validate_agent_payload(
            role="summarize",
            payload=summary_input,
            direction="input",
        )
        write_budgeted_trace_event(
            {
                "event": "handoff",
                "from": "research",
                "to": "summarize",
                "component": "labs.lab_06.agents.summarize_agent",
                "operation": "run",
                "contract_fields": [
                    "sources",
                    "prior_report",
                    "candidate_constraints",
                    "user_request",
                    "skill_prompt",
                ],
            },
            budget,
            config,
        )
        summary_output = summarize_agent.run(summary_input)
        validate_agent_payload(
            role="summarize",
            payload=summary_output,
            direction="output",
        )
        report = FitGapReport.model_validate(summary_output["fit_gap_report"])
        write_budgeted_trace_event(
            {
                "event": "summary_output",
                "agent": "summarize",
                "component": "labs.lab_02.src.schemas.FitGapReport",
                "operation": "model_validate",
                "fit_gap_report": report.model_dump(),
                "evidence_notes": summary_output.get("evidence_notes", []),
                "prep_plan": summary_output["prep_plan"],
            },
            budget,
            config,
        )
        write_trace_event(
            {
                "event": "evidence",
                "agent": "summarize",
                "component": "labs.lab_04.src.evidence",
                "operation": "build_evidence_notes",
                "semantic_key": "evidence.verify_claims",
                "source_ids": selected_source_ids,
                "evidence_count": len(summary_output.get("evidence_notes", [])),
            }
        )

        action_input = {
            "fit_gap_summary": report.fit_summary,
            "prep_plan": summary_output["prep_plan"],
            "requested_action": requested_action,
            "user_request": user_request,
            "skill_prompt": skill_prompt,
        }
        validate_agent_payload(
            role="action",
            payload=action_input,
            direction="input",
        )
        write_budgeted_trace_event(
            {
                "event": "handoff",
                "from": "summarize",
                "to": "action",
                "component": "labs.lab_06.agents.action_agent",
                "operation": "run",
                "contract_fields": [
                    "fit_gap_summary",
                    "prep_plan",
                    "requested_action",
                    "user_request",
                    "skill_prompt",
                ],
            },
            budget,
            config,
        )
        try:
            action_context_budget = action_agent.measure_action_prompt(
                action_input,
                budget_tokens=CONTEXT_BUDGET_TOKENS,
            )
        except ValueError as exc:
            estimated_tokens = action_agent.estimate_action_prompt_tokens(action_input)
            write_trace_event(
                {
                    "event": "context_budget",
                    "agent": "action",
                    "component": "labs.lab_06.agents.action_agent",
                    "operation": "measure_action_prompt",
                    "semantic_key": "context.measure_action_prompt",
                    "status": "failed",
                    "budget_tokens": CONTEXT_BUDGET_TOKENS,
                    "estimated_tokens": estimated_tokens,
                    "remaining_tokens": CONTEXT_BUDGET_TOKENS - estimated_tokens,
                }
            )
            raise ContextBudgetExceeded(str(exc)) from exc
        write_trace_event(
            {
                "event": "context_budget",
                "agent": "action",
                "component": "labs.lab_06.agents.action_agent",
                "operation": "measure_action_prompt",
                "semantic_key": "context.measure_action_prompt",
                "status": "completed",
                **action_context_budget,
            }
        )
        action_output = action_agent.run(action_input)
        validate_agent_payload(
            role="action",
            payload=action_output,
            direction="output",
        )
        write_budgeted_trace_event(
            {
                "event": "action_draft",
                "agent": "action",
                "component": "labs.lab_03.src.policies",
                "operation": "draft_action",
                "semantic_key": "policy.classify_action",
                "action_type": action_output["action_type"],
                "status": action_output["status"],
                "reason": action_output.get("reason", ""),
                "content": action_output["content"],
            },
            budget,
            config,
        )
        unsupported_claim = action_output["status"] == "blocked"
        decision = require_approval(
            action_output["action_type"],
            f"{user_request}\n\n{action_output['content']}",
            unsupported_claim=unsupported_claim,
        )
        write_budgeted_trace_event(
            {
                "event": "approval_decision",
                "agent": "action",
                "component": "labs.lab_06.guardrails",
                "operation": "require_approval",
                "status": decision.status,
                "reason": decision.reason,
            },
            budget,
            config,
        )
        budget.model_calls = model.calls_used()
        write_trace_event(
            {
                "event": "model_usage",
                "component": "labs.lab_01.src.model_client.ModelClient",
                "operation": "complete",
                "mode": model.mode(),
                "model_calls": model.calls_used(),
            }
        )
        write_trace_event(
            {
                "event": "stop",
                "status": "completed",
                "component": "labs.lab_06.agents.coordinator",
                "operation": "stop",
                "reason": "completed",
                "budget": budget_snapshot(budget, config),
            }
        )
        return {
            "profile": profile,
            "job_description": job_description,
            "research": research_output,
            "context_budget": context_budget,
            "action_context_budget": action_context_budget,
            "summary": summary_output,
            "action": action_output,
            "approval": decision.model_dump(),
            "model_mode": model.mode(),
            "budget": budget_snapshot(budget),
            "stop_reason": "completed",
        }
    except Exception as exc:
        budget.model_calls = model.calls_used()
        write_trace_event(
            {
                "event": "stop",
                "status": "failed",
                "component": "labs.lab_06.agents.coordinator",
                "operation": "stop",
                "reason": failure_stop_reason(exc),
                "error_type": type(exc).__name__,
                "budget": budget_snapshot(budget, config),
            }
        )
        raise
    finally:
        model.reset_trace_writer(model_trace_token)
        _active_trace_path.reset(trace_token)


def run_demo() -> dict[str, Any]:
    skill = load_skill(use_skill=True)
    payload = run_workflow(
        profile={"id": "profile-synthetic-001", "headline": "Backend engineer"},
        job_description={"id": "jd-synthetic-001", "title": "AI Tools Engineer", "company": "Northstar Systems"},
        source_items=None,
        prior_report={
            "fit_summary": "Partial fit.",
            "strengths": ["Python", "API integration"],
            "gaps": ["Direct production agent evidence"],
            "risks": ["Unsupported experience claims"],
            "missing_info": ["Target seniority"],
            "recommended_next_steps": ["Collect project evidence", "Practice LLM evaluation examples"],
        },
        requested_action="send_email",
        user_request="Draft and send outreach with an unsupported production migration claim.",
        skill_prompt=skill_prompt_block(skill),
    )
    print("agent=research")
    print("agent=summarize")
    print("agent=action")
    print(f"mode={payload['model_mode']}")
    print(f"model_calls={payload['budget']['model_calls']}")
    print(f"approval={payload['approval']['status']}")
    print("trace=artifacts/lab_06/multi_agent_trace.jsonl")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if not args.demo:
        raise SystemExit("Use --demo for the Lab 6 coordinator flow.")
    run_demo()


if __name__ == "__main__":
    main()
