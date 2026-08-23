from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

import labs.shared.artifacts as artifacts
from labs.lab_01.src.model_client import ModelClient
from labs.lab_03.src.policies import DraftAction
from labs.lab_03.src.tools import SourceResult
from labs.shared.config import Settings
from labs.lab_06.web_adapter import Lab06Adapter, build_multi_agent_capability, trace_to_events
from labs.shared.web.contracts import ArtifactLink, ChatRequest, HarnessEvent
from labs.shared.web.errors import StageExecutionError
from labs.shared.web.materials import MaterialStore
from labs.lab_06.agents import action_agent, research_agent, summarize_agent
from labs.lab_06.agents import coordinator
from labs.lab_06.config import MultiAgentConfig, load_config
from labs.lab_06.contracts import (
    AgentContract,
    AgentInstruction,
    ContractPayloadError,
    validate_contract,
    validate_contract_payload,
    validate_instruction,
)
from labs.lab_06.errors import WorkflowStopError
from labs.lab_06.guardrails import require_approval


TEST_INSTRUCTION = AgentInstruction(
    role="Test one bounded role.",
    objective="Exercise the shared model boundary.",
    boundary="Do not call external actions.",
)


def test_agent_instructions_and_contracts_are_filled() -> None:
    for module in (research_agent, summarize_agent, action_agent):
        validate_instruction(module.INSTRUCTION)
        assert module.INSTRUCTION.render()
        validate_contract(module.CONTRACT)
        assert module.CONTRACT.failure_statuses

    assert sorted(research_agent.CONTRACT.input_fields) == sorted(
        ["query", "search_query", "source_items"]
    )
    assert sorted(research_agent.CONTRACT.output_fields) == sorted(
        ["search_query", "sources", "source_ids", "source_snippets"]
    )
    assert sorted(summarize_agent.CONTRACT.input_fields) == sorted(
        [
            "sources",
            "prior_report",
            "candidate_constraints",
            "user_request",
            "skill_prompt",
        ]
    )
    assert sorted(summarize_agent.CONTRACT.output_fields) == sorted(
        ["fit_gap_report", "evidence_notes", "prep_plan"]
    )
    assert sorted(action_agent.CONTRACT.input_fields) == sorted(
        [
            "fit_gap_summary",
            "prep_plan",
            "requested_action",
            "user_request",
            "skill_prompt",
        ]
    )
    assert sorted(action_agent.CONTRACT.output_fields) == sorted(
        ["action_type", "status", "content", "reason"]
    )


def test_plain_text_cannot_replace_a_structured_agent_instruction() -> None:
    with pytest.raises(ValueError, match="role, objective, and boundary"):
        validate_instruction("asdf")  # type: ignore[arg-type]


def test_contract_payload_error_reports_validation_fields() -> None:
    contract = AgentContract(
        input_fields=["query", "source_items"],
        output_fields=["sources"],
        failure_statuses=["invalid_input"],
        trace_events=["contract_validation"],
    )

    with pytest.raises(ContractPayloadError) as captured:
        validate_contract_payload(contract, {"query": "Python"}, direction="input")

    validation = captured.value.validation
    assert validation.required_fields == ["query", "source_items"]
    assert validation.provided_fields == ["query"]
    assert validation.missing_fields == ["source_items"]


def test_stop_reason_uses_structured_error_metadata() -> None:
    error = WorkflowStopError(
        "message text can change",
        stop_reason="invalid_budget_config",
    )

    assert coordinator.failure_stop_reason(error) == "invalid_budget_config"
    assert coordinator.failure_stop_reason(
        ValueError("Lab 6 max_turns budget exceeded.")
    ) == "workflow_failed"


def test_budget_limits_are_positive() -> None:
    config = load_config()

    assert config.max_turns > 0
    assert config.max_tool_calls > 0
    assert config.max_model_calls > 0


def test_coordinator_enforces_budget_limits(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict] = []
    monkeypatch.setattr(coordinator, "validate_agents", lambda: None)
    monkeypatch.setattr(artifacts, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(coordinator, "write_trace_event", events.append)
    monkeypatch.setattr(coordinator, "load_config", lambda: MultiAgentConfig(max_turns=1, max_tool_calls=8, max_model_calls=6))

    with pytest.raises(ValueError, match="max_turns"):
        coordinator.run_demo()

    stop = events[-1]
    assert stop["event"] == "stop"
    assert stop["status"] == "failed"
    assert stop["reason"] == "orchestration_event_budget_exceeded"
    assert stop["error_type"] == "BudgetExceeded"
    assert "error" not in stop
    assert stop["budget"]["turns"] == stop["budget"]["orchestration_events"] == 2
    assert stop["budget"]["turn_budget_unit"] == "orchestration_event"
    assert stop["budget"]["budget_scope"] == (
        "lab_06_coordinator_and_role_owned_operations"
    )
    assert stop["budget"]["excluded_operations"] == [
        "inherited_lab_02_model_calls",
        "inherited_lab_04_model_calls",
        "inherited_mcp_protocol_operations",
    ]
    assert stop["budget"]["limits"]["max_turns"] == 1


def test_missing_agent_input_is_traced_before_run(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict] = []
    research_called = False
    boundary_called = False
    contract = research_agent.CONTRACT.model_copy(
        update={
            "input_fields": [
                *research_agent.CONTRACT.input_fields,
                "required_runtime_context",
            ]
        }
    )

    def research_run(_input: dict) -> dict:
        nonlocal research_called
        research_called = True
        return {}

    def inspect_query(_query: str) -> list[dict]:
        nonlocal boundary_called
        boundary_called = True
        return []

    monkeypatch.setattr(research_agent, "CONTRACT", contract)
    monkeypatch.setattr(research_agent, "plan_query", lambda _input: "planned query")
    monkeypatch.setattr(research_agent, "run", research_run)
    monkeypatch.setattr(artifacts, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(coordinator, "write_trace_event", events.append)

    with pytest.raises(ValueError, match="required_runtime_context"):
        coordinator.run_workflow(
            profile={"id": "profile-contract-input"},
            job_description={"id": "job-contract-input"},
            source_items=None,
            prior_report={},
            requested_action="outreach_draft",
            user_request="Research this role.",
            skill_prompt="Follow the evidence rules.",
            on_planned_query=inspect_query,
        )

    failed = next(
        event
        for event in events
        if event.get("event") == "contract_validation"
        and event.get("agent") == "research"
        and event.get("direction") == "input"
    )
    assert failed["operation"] == "validate_input"
    assert failed["status"] == "failed"
    assert failed["missing_fields"] == ["required_runtime_context"]
    assert "required_runtime_context" in failed["required_fields"]
    assert "required_runtime_context" not in failed["provided_fields"]
    assert research_called is False
    assert boundary_called is False
    assert events[-1]["reason"] == "contract_validation_failed"
    assert events[-1]["error_type"] == "ContractPayloadError"
    assert "error" not in events[-1]


def test_missing_agent_output_is_traced_before_handoff(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict] = []
    required_field = "required_runtime_output"
    output_fields = list(research_agent.CONTRACT.output_fields)
    contract = research_agent.CONTRACT.model_copy(
        update={"output_fields": [*output_fields, required_field]}
    )
    monkeypatch.setattr(research_agent, "CONTRACT", contract)
    monkeypatch.setattr(research_agent, "plan_query", lambda _input: "planned query")
    monkeypatch.setattr(
        research_agent,
        "run",
        lambda input_data: {
            "search_query": input_data["search_query"],
            "sources": [],
            "source_ids": [],
            "source_snippets": [],
        },
    )
    monkeypatch.setattr(artifacts, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(coordinator, "write_trace_event", events.append)

    with pytest.raises(ValueError, match=required_field):
        coordinator.run_demo()

    validations = [
        event
        for event in events
        if event.get("event") == "contract_validation"
        and event.get("agent") == "research"
    ]
    assert [(event["direction"], event["status"]) for event in validations] == [
        ("input", "completed"),
        ("output", "failed"),
    ]
    assert validations[-1]["operation"] == "validate_output"
    assert validations[-1]["missing_fields"] == [required_field]
    tool_result_index = next(
        index
        for index, event in enumerate(events)
        if event.get("event") == "tool_result"
    )
    validation_index = events.index(validations[-1])
    assert tool_result_index < validation_index
    assert not any(
        event.get("event") == "handoff" and event.get("to") == "summarize"
        for event in events
    )
    assert events[-1]["reason"] == "contract_validation_failed"
    assert events[-1]["error_type"] == "ContractPayloadError"
    assert "error" not in events[-1]


def test_malformed_research_output_does_not_report_zero_sources(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict] = []
    monkeypatch.setattr(research_agent, "plan_query", lambda _input: "planned query")
    monkeypatch.setattr(
        research_agent,
        "run",
        lambda input_data: {
            "search_query": input_data["search_query"],
            "sources": [],
            "source_snippets": [],
        },
    )
    monkeypatch.setattr(artifacts, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(coordinator, "write_trace_event", events.append)

    with pytest.raises(ValueError, match="source_ids"):
        coordinator.run_demo()

    tool_result = next(
        event for event in events if event.get("event") == "tool_result"
    )
    assert tool_result["status"] == "unknown"
    assert tool_result["source_count"] is None
    assert tool_result["source_ids"] is None


def test_complete_workflow_requires_three_model_slots(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict] = []
    monkeypatch.setattr(artifacts, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(coordinator, "write_trace_event", events.append)
    monkeypatch.setattr(
        coordinator,
        "load_config",
        lambda: MultiAgentConfig(
            max_turns=6,
            max_tool_calls=8,
            max_model_calls=2,
        ),
    )
    monkeypatch.setattr(
        "labs.lab_06.model.load_settings",
        lambda: Settings(model="offline-test", google_api_key=""),
    )

    with pytest.raises(ValueError, match="max_model_calls"):
        coordinator.run_demo()

    model_events = [event for event in events if event.get("event") == "model_call"]
    assert [event["agent"] for event in model_events] == [
        "research",
        "summarize",
        "action",
    ]
    assert [event["status"] for event in model_events] == [
        "completed",
        "completed",
        "failed",
    ]
    assert model_events[-1]["error_type"] == "BudgetExceeded"
    assert events[-1]["reason"] == "model_call_budget_exceeded"
    assert "error" not in events[-1]


def test_stop_trace_does_not_include_validation_payload(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict] = []
    monkeypatch.setattr(
        summarize_agent,
        "run",
        lambda _input: {
            "fit_gap_report": {"fit_summary": "private candidate text"},
            "evidence_notes": [],
            "prep_plan": {"days": []},
        },
    )
    monkeypatch.setattr(artifacts, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(coordinator, "write_trace_event", events.append)

    with pytest.raises(ValueError, match="validation error"):
        coordinator.run_demo()

    stop = events[-1]
    assert stop["event"] == "stop"
    assert stop["reason"] == "workflow_failed"
    assert stop["error_type"] == "ValidationError"
    assert "error" not in stop
    assert "private candidate text" not in json.dumps(stop)


@pytest.mark.parametrize("fail_on_call", [1, 2, 3])
def test_failed_provider_model_attempt_is_traced_for_each_agent(
    fail_on_call: int,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from labs.lab_06 import model

    events: list[dict] = []
    provider_calls = 0

    def fake_provider_call(_self, _messages) -> str:
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == fail_on_call:
            raise RuntimeError("synthetic provider failure")
        if provider_calls == 1:
            return "planned Python evidence query"
        return "Grounded fit summary."

    monkeypatch.setattr(artifacts, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(coordinator, "write_trace_event", events.append)
    monkeypatch.setattr(
        model,
        "load_settings",
        lambda: Settings(model="fake", google_api_key="test-key"),
    )
    monkeypatch.setattr(ModelClient, "complete", fake_provider_call)

    with pytest.raises(RuntimeError, match="synthetic provider failure"):
        coordinator.run_demo()

    model_events = [event for event in events if event.get("event") == "model_call"]
    assert [event["agent"] for event in model_events] == [
        "research",
        "summarize",
        "action",
    ][:fail_on_call]
    assert [event["status"] for event in model_events] == [
        *(["completed"] * (fail_on_call - 1)),
        "failed",
    ]
    assert model_events[-1]["error_type"] == "RuntimeError"
    assert model_events[-1]["model_calls"] == fail_on_call
    assert events[-1]["reason"] == "workflow_failed"
    assert events[-1]["error_type"] == "RuntimeError"
    assert "error" not in events[-1]


def test_concurrent_trace_writers_keep_request_local_paths(tmp_path) -> None:
    barrier = Barrier(2)

    def write_one(path, marker: str) -> None:
        token = coordinator._active_trace_path.set(path)
        try:
            barrier.wait()
            coordinator.write_trace_event({"event": "marker", "marker": marker})
        finally:
            coordinator._active_trace_path.reset(token)

    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda item: write_one(*item), [(first, "first"), (second, "second")]))

    assert json.loads(first.read_text(encoding="utf-8"))["marker"] == "first"
    assert json.loads(second.read_text(encoding="utf-8"))["marker"] == "second"


def test_unknown_trace_status_is_not_reported_as_completed(tmp_path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"event":"tool_result","status":"unexpected"}\n', encoding="utf-8")

    [event] = trace_to_events(trace)

    assert event.status == "failed"


def test_failed_contract_validation_is_not_summarized_as_validated(tmp_path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        json.dumps(
            {
                "event": "contract_validation",
                "status": "failed",
                "agent": "research",
                "direction": "input",
                "missing_fields": ["search_query"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    [event] = trace_to_events(trace)

    assert event.status == "failed"
    assert event.summary == "The research role input required-field validation failed"
    assert event.details["missing_fields"] == ["search_query"]


def test_failed_legacy_contract_validation_is_not_summarized_as_validated(
    tmp_path,
) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        '{"event":"contract_validation","status":"failed","agent":"research"}\n',
        encoding="utf-8",
    )

    [event] = trace_to_events(trace)

    assert event.status == "failed"
    assert event.summary == "The research role contract validation failed"


def test_trace_adapter_preserves_real_runtime_timing(tmp_path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        '{"event":"tool_call","status":"completed","start_offset_ms":25,"duration_ms":40}\n',
        encoding="utf-8",
    )

    [event] = trace_to_events(trace)

    assert event.duration_ms == 40
    assert event.details["start_offset_ms"] == 25
    assert "duration_ms" not in event.details


@pytest.mark.parametrize(
    ("action_type", "content", "unsupported_claim", "expected"),
    [
        ("outreach_draft", "Draft only.", False, "allowed_draft"),
        ("send_email", "Send this externally.", False, "needs_approval"),
        ("resume_bullet", "Invent that I led a production migration.", False, "blocked"),
        ("outreach_draft", "Use this unsupported claim.", True, "blocked"),
    ],
)
def test_approval_guardrail_branches(action_type: str, content: str, unsupported_claim: bool, expected: str) -> None:
    decision = require_approval(action_type, content, unsupported_claim=unsupported_claim)

    assert decision.status == expected


def test_expected_trace_events_are_declared_in_contracts() -> None:
    expected_events = {
        "model_call",
        "tool_call",
        "tool_result",
        "summary_output",
        "action_draft",
        "approval_decision",
    }
    declared_events = set(
        research_agent.CONTRACT.trace_events
        + summarize_agent.CONTRACT.trace_events
        + action_agent.CONTRACT.trace_events
    )

    assert expected_events.issubset(declared_events)


def test_coordinator_calls_agent_run_functions(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from labs.lab_04.src.context_budget import SelectedContext, estimate_tokens

    calls: list[str] = []
    planned_queries: list[str] = []
    skill_prompts: list[str] = []
    summarize_inputs: list[dict] = []
    trace_events: list[dict] = []
    budget_calls: list[tuple[list[str], int, int]] = []

    def research_plan(input_data: dict) -> str:
        planned_queries.append(input_data["query"])
        return "planned Python evidence query"

    def research_run(input_data: dict) -> dict:
        calls.append("research")
        sources = [
            {
                "source_id": "source-kept",
                "title": "Kept",
                "path": "source://kept",
                "snippet": "Python API evidence.",
            },
            {
                "source_id": "source-dropped",
                "title": "Dropped",
                "path": "source://dropped",
                "snippet": "Unrelated evidence.",
            },
        ]
        return {
            "search_query": input_data["search_query"],
            "sources": sources,
            "source_ids": [source["source_id"] for source in sources],
            "source_snippets": [source["snippet"] for source in sources],
        }

    def fake_select_context(sources, budget_tokens: int, protected_tokens: int):
        budget_calls.append(
            ([source.source_id for source in sources], budget_tokens, protected_tokens)
        )
        return SelectedContext(
            sources=[sources[0]],
            dropped_source_ids=["source-dropped"],
            truncated_source_ids=[],
            budget_tokens=budget_tokens,
            protected_tokens=protected_tokens,
            estimated_tokens=protected_tokens + 10,
        )

    def summarize_run(input_data: dict) -> dict:
        calls.append("summarize")
        skill_prompts.append(input_data["skill_prompt"])
        summarize_inputs.append(input_data)
        return {
            "fit_gap_report": {
                "fit_summary": "Partial fit.",
                "strengths": ["Python"],
                "gaps": ["Agent framework evidence"],
                "risks": ["Unsupported production claims"],
                "missing_info": ["Timeline"],
                "recommended_next_steps": ["Collect evidence."],
            },
            "evidence_notes": [],
            "prep_plan": {"days": [{"day": 1, "task": "Collect evidence."}]},
        }

    def action_run(input_data: dict) -> dict:
        calls.append("action")
        skill_prompts.append(input_data["skill_prompt"])
        return {
            "action_type": "outreach_draft",
            "status": "needs_approval",
            "content": "Draft outreach.",
            "reason": "External action needs approval.",
        }

    monkeypatch.setattr(research_agent, "plan_query", research_plan)
    monkeypatch.setattr(research_agent, "run", research_run)
    monkeypatch.setattr(summarize_agent, "run", summarize_run)
    monkeypatch.setattr(action_agent, "run", action_run)
    monkeypatch.setattr(coordinator, "validate_agents", lambda: None)
    monkeypatch.setattr(artifacts, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(coordinator, "write_trace_event", trace_events.append)
    monkeypatch.setattr(coordinator, "select_context", fake_select_context)

    payload = coordinator.run_demo()

    assert calls == ["research", "summarize", "action"]
    assert planned_queries == [
        "Draft and send outreach with an unsupported production migration claim."
    ]
    assert len(skill_prompts) == 2
    assert all("Do not invent experience" in prompt for prompt in skill_prompts)
    assert budget_calls == [
        (
            ["source-kept", "source-dropped"],
            4_000,
            summarize_agent.summarize_prompt_protected_tokens(
                skill_prompts[0],
                ["Do not invent experience."],
                "Draft and send outreach with an unsupported production migration claim.",
            ),
        )
    ]
    assert budget_calls[0][2] > estimate_tokens(skill_prompts[0])
    assert [source["source_id"] for source in summarize_inputs[0]["sources"]] == [
        "source-kept"
    ]
    assert summarize_inputs[0]["source_ids"] == ["source-kept"]
    assert summarize_inputs[0]["user_request"] == (
        "Draft and send outreach with an unsupported production migration claim."
    )
    assert payload["context_budget"]["dropped_source_ids"] == ["source-dropped"]
    assert payload["budget"]["turns"] == payload["budget"]["orchestration_events"]
    assert payload["budget"]["turn_budget_unit"] == "orchestration_event"
    contract_events = [
        event
        for event in trace_events
        if event.get("event") == "contract_validation"
    ]
    assert [
        (event["agent"], event["direction"], event["status"])
        for event in contract_events
    ] == [
        ("research", "input", "completed"),
        ("research", "output", "completed"),
        ("summarize", "input", "completed"),
        ("summarize", "output", "completed"),
        ("action", "input", "completed"),
        ("action", "output", "completed"),
    ]
    operations = [event.get("operation") for event in trace_events]
    assert operations.index("search_sources") < operations.index("select_context")
    assert operations.index("select_context") < next(
        index
        for index, event in enumerate(trace_events)
        if event.get("event") == "handoff" and event.get("to") == "summarize"
    )
    context_event = next(
        event for event in trace_events if event.get("operation") == "select_context"
    )
    assert context_event["semantic_key"] == "context.select_budgeted"
    assert context_event["agent"] == "research"
    assert context_event["request_context_tokens"] > 0
    assert context_event["protected_tokens"] == (
        context_event["skill_tokens"]
        + context_event["request_context_tokens"]
        + context_event["prompt_scaffold_tokens"]
    )


def test_model_budget_blocks_call_before_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    from labs.lab_06 import model

    monkeypatch.setattr(model, "load_settings", lambda: Settings(model="fake", google_api_key=""))

    model.reset_session(max_model_calls=1)
    try:
        model.complete(
            "Summarize the fit.",
            offline_text="First response.",
            instruction=TEST_INSTRUCTION,
        )
        assert model.calls_used() == 1

        with pytest.raises(ValueError, match="max_model_calls"):
            model.complete(
                "Draft outreach.",
                offline_text="Second response.",
                instruction=TEST_INSTRUCTION,
            )

        # The over-budget call was rejected before consuming a slot.
        assert model.calls_used() == 1
    finally:
        model.reset_session()


def test_model_budget_is_request_local_across_concurrent_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    from labs.lab_06 import model

    monkeypatch.setattr(model, "load_settings", lambda: Settings(model="fake", google_api_key=""))
    barrier = Barrier(2)

    def run_with_budget(limit: int) -> tuple[int, bool]:
        model.reset_session(max_model_calls=limit)
        barrier.wait()
        for index in range(limit):
            model.complete(
                f"request-{limit}-{index}",
                offline_text="offline",
                instruction=TEST_INSTRUCTION,
            )
        try:
            model.complete(
                "over budget",
                offline_text="offline",
                instruction=TEST_INSTRUCTION,
            )
        except ValueError:
            blocked = True
        else:
            blocked = False
        return model.calls_used(), blocked

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run_with_budget, [1, 2]))

    assert results == [(1, True), (2, True)]


def test_lab_6_model_reuses_lab_1_boundary_in_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from labs.lab_06 import model

    calls: list[list[dict[str, str]]] = []
    monkeypatch.setattr(model, "load_settings", lambda: Settings(model="fake", google_api_key="test-key"))
    monkeypatch.setattr(ModelClient, "complete", lambda self, messages: calls.append(messages) or "Lab 1 response")

    model.reset_session(max_model_calls=1)
    try:
        assert model.complete(
            "Summarize fit.",
            offline_text="offline",
            instruction=TEST_INSTRUCTION,
        ) == "Lab 1 response"
        assert calls[0][0] == {
            "role": "system",
            "content": model.render_system_prompt(TEST_INSTRUCTION),
        }
        assert calls[0][1] == {"role": "user", "content": "Summarize fit."}
    finally:
        model.reset_session()


def test_research_agent_reuses_lab_3_source_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int, list[dict] | None]] = []
    model_inputs: list[tuple[str, AgentInstruction]] = []

    def fake_search(query: str, limit: int, source_items: list[dict] | None = None):
        calls.append((query, limit, source_items))
        return [
            SourceResult(
                source_id="source-current",
                title="Current source",
                path="job-material://source-current",
                snippet="Python API evidence.",
            )
        ]

    monkeypatch.setattr(research_agent, "search_sources", fake_search)
    monkeypatch.setattr(
        research_agent.model,
        "complete",
        lambda prompt, offline_text, *, instruction: (
            model_inputs.append((prompt, instruction)) or "planned Python API query"
        ),
    )
    source_items = [{"source_id": "input-current"}]

    input_data = {"query": "Python API", "limit": 2, "source_items": source_items}
    search_query = research_agent.plan_query(input_data)
    output = research_agent.run({**input_data, "search_query": search_query})

    assert model_inputs == [
        (research_agent.render_research_prompt("Python API"), research_agent.INSTRUCTION)
    ]
    assert calls == [("planned Python API query", 2, source_items)]
    assert output["search_query"] == "planned Python API query"
    assert output["source_ids"] == ["source-current"]
    assert output["sources"][0]["snippet"] == "Python API evidence."


def test_action_agent_reuses_lab_3_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    prompts: list[tuple[str, AgentInstruction]] = []
    monkeypatch.setattr(
        action_agent.model,
        "complete",
        lambda prompt, offline_text, *, instruction: (
            prompts.append((prompt, instruction)) or "Supported draft"
        ),
    )

    def fake_policy(action_type: str, content: str) -> DraftAction:
        calls.append((action_type, content))
        return DraftAction(
            action_type=action_type,
            content=content,
            status="needs_approval",
            reason="External action needs approval.",
        )

    monkeypatch.setattr(action_agent, "draft_action", fake_policy)
    output = action_agent.run(
        {
            "fit_gap_summary": "Partial fit",
            "prep_plan": {},
            "requested_action": "send_email",
            "user_request": "Send this now.",
            "skill_prompt": "Follow these rules:\n- Never invent experience.\n\n",
        }
    )

    assert "skill_prompt" in action_agent.CONTRACT.input_fields
    assert "Never invent experience." in prompts[0][0]
    assert "Send this now." in prompts[0][0]
    assert prompts[0][1] == action_agent.INSTRUCTION
    assert calls and calls[0][0] == "send_email"
    assert output["status"] == "needs_approval"
    assert output["content"] == "Supported draft"


@pytest.mark.parametrize(
    ("requested_action", "prompt_text", "fallback_prefix", "operation"),
    [
        ("outreach_draft", "factual outreach message", "Outreach draft", "draft_outreach"),
        ("resume_bullet", "factual resume bullet", "Resume bullet draft", "draft_resume_bullet"),
        ("prep_plan", "interview preparation plan", "Interview prep plan", "draft_prep_plan"),
    ],
)
def test_action_agent_branches_renderer_and_fallback_by_requested_action(
    requested_action: str,
    prompt_text: str,
    fallback_prefix: str,
    operation: str,
) -> None:
    input_data = {
        "fit_gap_summary": "Partial fit with Python evidence.",
        "prep_plan": {"days": [{"day": 1, "task": "Collect production evidence"}]},
        "requested_action": requested_action,
        "user_request": f"Create a {requested_action}.",
        "skill_prompt": "Follow these rules:\n- Never invent experience.\n\n",
    }

    assert prompt_text in action_agent.render_action_prompt(input_data)
    assert action_agent.offline_action_text(input_data).startswith(fallback_prefix)
    assert action_agent.action_model_operation(input_data)[0] == operation


def test_action_agent_measures_its_complete_prompt_and_rejects_overflow() -> None:
    input_data = {
        "fit_gap_summary": "Partial fit",
        "prep_plan": {"days": [{"day": 1, "task": "Collect evidence"}]},
        "requested_action": "send_email",
        "user_request": "Draft outreach for this role.",
        "skill_prompt": "Follow these rules:\n- Never invent experience.\n\n",
    }

    prompt = action_agent.render_action_prompt(input_data)
    budget = action_agent.measure_action_prompt(input_data, budget_tokens=4_000)

    complete_prompt = f"{action_agent.model.render_system_prompt(action_agent.INSTRUCTION)}\n{prompt}"
    assert budget["estimated_tokens"] == (len(complete_prompt) + 3) // 4
    assert budget["estimated_tokens"] <= budget["budget_tokens"]
    with pytest.raises(ValueError, match="action prompt.*context budget"):
        action_agent.measure_action_prompt(
            input_data,
            budget_tokens=budget["estimated_tokens"] - 1,
        )


def test_action_budget_protects_the_role_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_data = {
        "fit_gap_summary": "Partial fit",
        "prep_plan": {"days": []},
        "requested_action": "outreach_draft",
        "user_request": "Draft outreach.",
        "skill_prompt": "Follow these rules:\n- Never invent experience.\n\n",
    }
    original_tokens = action_agent.estimate_action_prompt_tokens(input_data)
    monkeypatch.setattr(
        action_agent,
        "INSTRUCTION",
        AgentInstruction(
            role=(
                "Create a carefully bounded factual action draft for the current request "
                "while preserving every supplied evidence relationship."
            ),
            objective=(
                "Preserve the supplied fit evidence, requested draft format, candidate "
                "constraints, and explicit next steps in the local result."
            ),
            boundary=(
                "Never invent facts, execute an external action, bypass approval, add "
                "unsupported metrics, or hide missing information from the user."
            ),
        ),
    )

    assert action_agent.estimate_action_prompt_tokens(input_data) > original_tokens


def test_coordinator_records_action_budget_overflow_before_aborting(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_events: list[dict] = []

    def overlong_summary(input_data: dict) -> dict:
        report = dict(input_data["prior_report"])
        report["fit_summary"] = "x" * 16_000
        return {
            "fit_gap_report": report,
            "evidence_notes": [],
            "prep_plan": {"days": []},
        }

    monkeypatch.setattr(summarize_agent, "run", overlong_summary)
    monkeypatch.setattr(artifacts, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(coordinator, "write_trace_event", trace_events.append)

    with pytest.raises(ValueError, match="action prompt.*context budget"):
        coordinator.run_demo()

    failed = next(
        event
        for event in trace_events
        if event.get("operation") == "measure_action_prompt"
    )
    assert failed["status"] == "failed"
    assert failed["estimated_tokens"] > failed["budget_tokens"] == 4_000
    assert "error" not in failed
    assert trace_events[-1]["reason"] == "context_budget_exceeded"
    assert trace_events[-1]["error_type"] == "ContextBudgetExceeded"
    assert "error" not in trace_events[-1]


def test_coordinator_records_summarize_budget_overflow_before_aborting(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_events: list[dict] = []
    summarize_called = False

    monkeypatch.setattr(
        research_agent,
        "run",
        lambda input_data: {
            "search_query": input_data["search_query"],
            "sources": [
                {
                    "source_id": "source-profile",
                    "title": "Profile",
                    "path": "profile.json",
                    "snippet": "Python API experience.",
                }
            ],
            "source_ids": ["source-profile"],
            "source_snippets": ["Python API experience."],
        },
    )

    def capture_summarize(_input: dict) -> dict:
        nonlocal summarize_called
        summarize_called = True
        return {}

    monkeypatch.setattr(summarize_agent, "run", capture_summarize)
    monkeypatch.setattr(artifacts, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(coordinator, "write_trace_event", trace_events.append)

    with pytest.raises(ValueError, match="protected.*budget"):
        coordinator.run_workflow(
            profile={"id": "profile-overflow"},
            job_description={"id": "jd-overflow"},
            source_items=None,
            prior_report={"fit_summary": "Partial fit."},
            requested_action="outreach_draft",
            user_request="x" * 20_000,
            skill_prompt="Follow the evidence rules.",
        )

    failed = next(
        event
        for event in trace_events
        if event.get("operation") == "select_context"
    )
    assert failed["status"] == "failed"
    assert failed["protected_tokens"] > failed["budget_tokens"] == 4_000
    assert "error" not in failed
    assert summarize_called is False
    assert trace_events[-1]["reason"] == "context_budget_exceeded"
    assert trace_events[-1]["error_type"] == "ContextBudgetExceeded"
    assert "error" not in trace_events[-1]


def test_lab_6_web_adapter_runs_complete_graph_without_prior_output_artifacts(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MaterialStore(tmp_path / "materials")
    workspace_id = "workspace_lab06test"
    store.create_text(
        workspace_id,
        "candidate_profile",
        "profile.md",
        "Candidate has Python API integration experience.",
        source="fixture",
    )
    store.create_text(
        workspace_id,
        "job_description",
        "jd.md",
        "AI Tools Engineer role requiring Python API experience.",
        source="fixture",
    )
    source = store.create_text(
        workspace_id,
        "web_source",
        "Current evidence",
        "Candidate has Python API integration experience.",
        source="fixture",
    )
    def fake_model_response(_self, messages) -> str:
        system_prompt = messages[0]["content"]
        if "bounded role inside a job-agent harness" not in system_prompt:
            return (
                '{"fit_summary":"Partial fit","strengths":["Python"],'
                '"gaps":["Production agents"],"risks":["Unsupported claims"],'
                '"missing_info":["Scale"],'
                '"recommended_next_steps":["Collect evidence","Practice evaluation examples"]}'
            )
        if research_agent.INSTRUCTION.role in system_prompt:
            return "planned Python evidence query"
        if summarize_agent.INSTRUCTION.role in system_prompt:
            return "Grounded fit summary."
        return "Supported outreach draft."

    monkeypatch.setattr(ModelClient, "complete", fake_model_response)
    monkeypatch.setattr(
        "labs.lab_03.src.model_tool_use.load_settings",
        lambda: Settings(model="offline-test", google_api_key=""),
    )
    monkeypatch.setattr(
        "labs.lab_06.model.load_settings",
        lambda: Settings(model="fake", google_api_key="test-key"),
    )
    monkeypatch.setattr(
        "labs.lab_04.src.job_research.load_settings",
        lambda: Settings(model="offline-test", google_api_key=""),
    )
    monkeypatch.setattr(artifacts, "ROOT_DIR", tmp_path)
    monkeypatch.setattr("labs.lab_02.src.state_store.ROOT_DIR", tmp_path)

    response = Lab06Adapter(store).chat(
        ChatRequest(
            stage="lab_06",
            session_id="session_lab06test",
            workspace_id=workspace_id,
            messages=[
                {
                    "role": "user",
                    "content": "Focus the research on my Python evidence.",
                },
                {
                    "role": "assistant",
                    "content": "I will keep the next run bounded.",
                },
                {
                    "role": "user",
                    "content": "Summarize fit and create an outreach draft only.",
                },
            ],
        )
    )

    assert response.status == "ok"
    assert response.state_summary["roles"] == ["research", "summarize", "action"]
    assert response.state_summary["stop_reason"] == "completed"
    assert response.state_summary["budget"]["tool_calls"] == 1
    assert response.state_summary["budget"]["model_calls"] == 3
    assert response.state_summary["budget"]["budget_scope"] == (
        "lab_06_coordinator_and_role_owned_operations"
    )
    assert response.state_summary["budget"]["turns"] == (
        response.state_summary["budget"]["orchestration_events"]
    )
    event_types = [event.type for event in response.events]
    assert event_types.count("handoff") >= 3
    assert "contract_validation" in event_types
    assert "tool_call" in event_types
    assert "context_budget" in event_types
    assert "approval_decision" in event_types
    contract_events = [
        event for event in response.events if event.type == "contract_validation"
    ]
    assert [
        (event.operation, event.summary)
        for event in contract_events
    ] == [
        ("validate_input", "Validated the research role input required fields"),
        ("validate_output", "Validated the research role output required fields"),
        ("validate_input", "Validated the summarize role input required fields"),
        ("validate_output", "Validated the summarize role output required fields"),
        ("validate_input", "Validated the action role input required fields"),
        ("validate_output", "Validated the action role output required fields"),
    ]
    agent_model_calls = [
        event
        for event in response.events
        if event.type == "model_call"
        and event.component.startswith("labs.lab_06.agents.")
    ]
    assert [event.details["agent"] for event in agent_model_calls] == [
        "research",
        "summarize",
        "action",
    ]
    assert [event.details["model_input"]["system_prompt"] for event in agent_model_calls] == [
        research_agent.model.render_system_prompt(research_agent.INSTRUCTION),
        summarize_agent.model.render_system_prompt(summarize_agent.INSTRUCTION),
        action_agent.model.render_system_prompt(action_agent.INSTRUCTION),
    ]
    assert all(
        event.details["model_input"]["user_prompt"] == "[redacted]"
        for event in agent_model_calls
    )
    assert "stop" in event_types
    mcp_events = [event for event in response.events if event.type == "capability_boundary"]
    assert [event.operation for event in mcp_events] == [
        "initialize",
        "tools/list",
    ]
    assert all(event.duration_ms is not None and event.duration_ms > 0 for event in mcp_events)
    assert response.events[-2].operation == "prepare_eval_target"
    assert response.events[-1].operation == "record_run_trace"
    job_research_event = next(
        event
        for event in response.events
        if event.component == "labs.lab_04.src.job_research.JobResearchModel"
    )
    assert job_research_event.operation == "select_mcp_tool"
    assert "Skipped job-board call" in job_research_event.summary
    mcp_provider_input = json.dumps(
        job_research_event.details["model_io"]["actual_provider_input"],
        ensure_ascii=False,
    )
    assert "planned Python evidence query" in mcp_provider_input
    assert "Focus the research on my Python evidence." not in mcp_provider_input
    research_tool_call = next(
        event
        for event in response.events
        if event.type == "tool_call" and event.operation == "search_sources"
    )
    workflow_query = research_tool_call.details["input"]["query"]
    assert workflow_query == "planned Python evidence query"
    pipeline_operations = [event.operation for event in response.events]
    assert pipeline_operations.index("plan_search_query") < pipeline_operations.index(
        "initialize"
    ) < pipeline_operations.index("search_sources")
    context_event = next(
        event for event in response.events if event.operation == "select_context"
    )
    assert context_event.details["protected_tokens"] > 0
    assert context_event.details["selected_source_ids"] == [source.material_id]
    assert response.state_summary["action_context_budget"]["estimated_tokens"] <= 4_000
    assert {artifact.label for artifact in response.artifacts} >= {
        "Multi-agent result",
        "Multi-agent call trace",
        "Lab 06 call trace",
    }
    assert not (tmp_path / "artifacts" / "lab_04" / "workspaces").exists()
    assert not (tmp_path / "artifacts" / "lab_05" / "workspaces").exists()


def test_lab_6_eval_delegates_to_lab_5_default_capability(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import labs.lab_06.web_adapter as lab_06_web_adapter

    captured: dict[str, object] = {}

    def fake_eval_capability(**kwargs) -> dict:
        captured.update(kwargs)
        kwargs["events"].append(
            HarnessEvent(
                sequence=1,
                type="eval",
                status="completed",
                component="labs.lab_05.src.evals",
                operation="evaluate_task",
                summary="Lab 5 default task passed",
                details={"lineage": "lab_05_default"},
            )
        )
        kwargs["artifacts"].append(
            ArtifactLink(label="Eval summary", path="artifacts/lab_06/eval.json")
        )
        return {
            "total": 3,
            "passed": 3,
            "failed": 0,
            "results": [
                {"task_id": "fake_experience", "passed": True},
                {"task_id": "grounded_local_draft", "passed": True},
                {"task_id": "external_send", "passed": True},
            ],
            "run": {"run_id": kwargs["run_id"], "stage": kwargs["stage_id"]},
        }

    monkeypatch.setattr(lab_06_web_adapter, "build_eval_capability", fake_eval_capability)
    monkeypatch.setattr(
        lab_06_web_adapter,
        "write_run_trace",
        lambda *_: ArtifactLink(label="Lab 06 call trace", path="artifacts/lab_06/trace.jsonl"),
    )

    response = lab_06_web_adapter.Lab06Adapter(
        MaterialStore(tmp_path / "materials")
    ).run_eval("workspace_lab06_eval")

    assert captured["stage_id"] == "lab_06"
    assert response.summary["run"] == {
        "run_id": response.run_id,
        "stage": "lab_06",
    }
    assert response.summary["total"] == 3
    assert response.summary["passed"] == 3
    assert {result["task_id"] for result in response.summary["results"]} == {
        "fake_experience",
        "grounded_local_draft",
        "external_send",
    }
    assert response.events[0].details["lineage"] == "lab_05_default"
    assert {artifact.label for artifact in response.artifacts} == {
        "Eval summary",
        "Lab 06 call trace",
    }


def test_lab_6_current_openings_flow_into_research_context_and_prep_action(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MaterialStore(tmp_path / "materials")
    workspace_id = "workspace_lab06_current_openings"
    store.create_text(
        workspace_id,
        "candidate_profile",
        "profile.md",
        "Candidate has Python API integration experience.",
        source="fixture",
    )
    store.create_text(
        workspace_id,
        "job_description",
        "jd.md",
        "Target role values Python and API experience.",
        source="fixture",
    )
    monkeypatch.setattr(
        "labs.lab_03.src.model_tool_use.load_settings",
        lambda: Settings(model="offline-test", google_api_key=""),
    )
    monkeypatch.setattr(
        "labs.lab_06.model.load_settings",
        lambda: Settings(model="offline-test", google_api_key=""),
    )
    monkeypatch.setattr(
        "labs.lab_04.src.job_research.load_settings",
        lambda: Settings(model="offline-test", google_api_key=""),
    )
    monkeypatch.setattr(
        ModelClient,
        "complete",
        lambda _self, _messages: json.dumps(
            {
                "fit_summary": "Partial fit with Python API evidence.",
                "strengths": ["Python", "API integration"],
                "gaps": ["Direct role evidence"],
                "risks": ["Unsupported claims"],
                "missing_info": ["Production scale"],
                "recommended_next_steps": ["Collect evidence", "Prepare examples"],
            }
        ),
    )
    monkeypatch.setattr(artifacts, "ROOT_DIR", tmp_path)
    monkeypatch.setattr("labs.lab_02.src.state_store.ROOT_DIR", tmp_path)

    response = Lab06Adapter(store).chat(
        ChatRequest(
            stage="lab_06",
            session_id="session_lab06_current_openings",
            workspace_id=workspace_id,
            messages=[
                {
                    "role": "user",
                    "content": "Research current openings at Stripe and create a prep plan.",
                }
            ],
        )
    )

    assert response.status == "ok"
    tool_call = next(
        event
        for event in response.events
        if event.type == "capability_boundary" and event.operation == "tools/call"
    )
    assert tool_call.details["tool"] == "list_openings"
    assert tool_call.details["arguments"]["company"] == "stripe"
    job_source_ids = [
        source_id
        for source_id in response.state_summary["research"]["source_ids"]
        if source_id.startswith("job-greenhouse-")
    ]
    assert job_source_ids
    assert set(job_source_ids) & set(
        response.state_summary["context_budget"]["selected_source_ids"]
    )
    draft = response.state_summary["draft_action"]
    assert draft["action_type"] == "prep_plan"
    assert draft["content"].startswith("Interview prep plan:")


def test_lab_6_web_adapter_reports_missing_raw_inputs_before_coordinator(tmp_path) -> None:
    adapter = Lab06Adapter(MaterialStore(tmp_path / "materials"))

    with pytest.raises(StageExecutionError) as captured:
        adapter.chat(
            ChatRequest(
                stage="lab_06",
                session_id="session_lab06failure",
                workspace_id="workspace_lab06failure",
                messages=[{"role": "user", "content": "Run the bounded workflow."}],
            )
        )

    event = captured.value.events[-1]
    assert event.status == "failed"
    assert event.component == "labs.lab_06.web_adapter"
    assert event.operation == "execute_complete_stage"


def test_lab_6_attributes_task_prompt_loading_failures(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import labs.lab_04.src.prompt_loader as prompt_loader
    import labs.lab_06.web_adapter as web_adapter
    from labs.lab_04.src.skill_loader import LoadedSkill

    monkeypatch.setattr(artifacts, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(web_adapter, "load_task_state", lambda *_: (None, False))
    monkeypatch.setattr(
        web_adapter,
        "build_structured_capability",
        lambda **_: {
            "profile": {"id": "profile-prompt-failure"},
            "job_description": {"id": "job-prompt-failure"},
            "report": {},
        },
    )
    monkeypatch.setattr(web_adapter, "fetch_pending_web_sources", lambda **_: None)
    monkeypatch.setattr(
        web_adapter,
        "load_student_skill",
        lambda: LoadedSkill(
            loaded=True,
            rules=["Never invent experience."],
            rule_count=1,
        ),
    )
    monkeypatch.setattr(
        prompt_loader,
        "load_task_prompt",
        lambda: (_ for _ in ()).throw(ValueError("Task prompt is invalid")),
    )

    with pytest.raises(StageExecutionError) as captured:
        Lab06Adapter(MaterialStore(tmp_path / "materials")).chat(
            ChatRequest(
                stage="lab_06",
                session_id="session_lab06_prompt_failure",
                workspace_id="workspace_lab06_prompt_failure",
                messages=[{"role": "user", "content": "Run the bounded workflow."}],
            )
        )

    event = captured.value.events[-1]
    assert event.type == "prompt"
    assert event.status == "failed"
    assert event.component == "labs.lab_04.prompts.grounded-job-research.md"
    assert event.operation == "load_task_prompt"
    assert event.details["semantic_key"] == "prompt.load_task_template"


def test_lab_6_web_adapter_reports_mcp_failures_at_the_capability_boundary(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from labs.lab_04.src.mcp_client_adapter import MCPBoundaryError

    monkeypatch.setattr(artifacts, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(
        "labs.lab_06.web_adapter.build_multi_agent_capability",
        lambda **_: (_ for _ in ()).throw(
            MCPBoundaryError("tools/list", RuntimeError("transport closed"))
        ),
    )

    with pytest.raises(StageExecutionError) as captured:
        Lab06Adapter(MaterialStore(tmp_path / "materials")).chat(
            ChatRequest(
                stage="lab_06",
                session_id="session_lab06mcp",
                workspace_id="workspace_lab06mcp",
                messages=[{"role": "user", "content": "Run the bounded workflow."}],
            )
        )

    event = captured.value.events[-1]
    assert event.type == "capability_boundary"
    assert event.component == "labs.lab_04.src.mcp_client_adapter.Client"
    assert event.operation == "tools/list"


def test_lab_6_preserves_completed_mcp_events_before_a_later_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import labs.lab_04.src.job_research as job_research
    import labs.lab_04.src.mcp_client_adapter as mcp_adapter
    import labs.lab_06.web_adapter as web_adapter
    from labs.lab_04.src.skill_loader import LoadedSkill

    monkeypatch.setattr(web_adapter, "load_task_state", lambda *_: (None, False))
    monkeypatch.setattr(
        web_adapter,
        "build_structured_capability",
        lambda **_: {
            "profile": {"id": "profile-mcp-failure"},
            "job_description": {"id": "job-mcp-failure"},
            "report": {},
        },
    )
    monkeypatch.setattr(web_adapter, "fetch_pending_web_sources", lambda **_: None)
    monkeypatch.setattr(
        web_adapter,
        "load_student_skill",
        lambda: LoadedSkill(loaded=True, rules=["Never invent experience."], rule_count=1),
    )
    monkeypatch.setattr(
        job_research,
        "research_job_board",
        lambda **_: (_ for _ in ()).throw(
            mcp_adapter.MCPBoundaryError(
                "tools/call",
                RuntimeError("transport closed"),
                completed_operations=[
                    {
                        "operation": "initialize",
                        "summary": "Negotiated MCP protocol",
                        "duration_ms": 1,
                        "details": {"semantic_key": "capability.mcp.initialize"},
                    },
                    {
                        "operation": "tools/list",
                        "summary": "Discovered MCP tools",
                        "duration_ms": 2,
                        "details": {"semantic_key": "capability.mcp.tools.list"},
                    },
                ],
            )
        ),
    )
    monkeypatch.setattr(
        "labs.lab_06.model.load_settings",
        lambda: Settings(model="offline-test", google_api_key=""),
    )

    events = []
    trace_path = tmp_path / "trace.jsonl"
    with pytest.raises(mcp_adapter.MCPBoundaryError):
        build_multi_agent_capability(
            materials=MaterialStore(tmp_path / "materials"),
            request=ChatRequest(
                stage="lab_06",
                session_id="session_lab06_mcp_events",
                workspace_id="workspace_lab06_mcp_events",
                messages=[{"role": "user", "content": "Run the workflow."}],
            ),
            run_id="run_lab06_mcp_events",
            stage_id="lab_06",
            events=events,
            artifacts=[],
            trace_path=trace_path,
        )

    assert [event.operation for event in events[-2:]] == [
        "load_skill",
        "load_task_prompt",
    ]
    assert [event.operation for event in trace_to_events(trace_path)[-5:]] == [
        "plan_search_query",
        "validate_input",
        "initialize",
        "tools/list",
        "stop",
    ]


def test_summarize_agent_includes_skill_rules_and_request_in_the_model_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[tuple[str, AgentInstruction]] = []
    monkeypatch.setattr(
        summarize_agent.model,
        "complete",
        lambda prompt, offline_text, *, instruction: (
            prompts.append((prompt, instruction)) or "Grounded fit summary."
        ),
    )

    summarize_agent.run(
        {
            "sources": [
                {
                    "source_id": "source-1",
                    "title": "Profile",
                    "path": "profile.md",
                    "snippet": "Python API experience.",
                }
            ],
            "source_ids": ["source-1"],
            "source_snippets": ["Python API experience."],
            "prior_report": {
                "fit_summary": "Partial fit.",
                "strengths": ["Python"],
                "gaps": ["Production evidence"],
                "risks": ["Unsupported claims"],
                "missing_info": ["Scale"],
                "recommended_next_steps": ["Collect evidence"],
            },
            "candidate_constraints": ["Do not invent experience."],
            "user_request": "Summarize my Python API fit for this role.",
            "skill_prompt": "Follow these rules:\n- Never invent experience.\n\n",
        }
    )

    assert "skill_prompt" in summarize_agent.CONTRACT.input_fields
    assert "Never invent experience." in prompts[0][0]
    assert "Summarize my Python API fit for this role." in prompts[0][0]
    assert "source_id=source-1" in prompts[0][0]
    assert prompts[0][1] == summarize_agent.INSTRUCTION
    assert "user_request" in summarize_agent.CONTRACT.input_fields
    protected_with_request = summarize_agent.summarize_prompt_protected_tokens(
        "Follow these rules:\n- Never invent experience.\n\n",
        ["Do not invent experience."],
        "Summarize my Python API fit for this role.",
    )
    protected_without_request = summarize_agent.summarize_prompt_protected_tokens(
        "Follow these rules:\n- Never invent experience.\n\n",
        ["Do not invent experience."],
        "",
    )
    assert protected_with_request > protected_without_request > 0


def test_summarize_budget_protects_the_role_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_prompt = "Follow these rules:\n- Never invent experience.\n\n"
    constraints = ["Do not invent experience."]
    original_tokens = summarize_agent.summarize_prompt_protected_tokens(
        skill_prompt,
        constraints,
        "Summarize my fit.",
    )
    monkeypatch.setattr(
        summarize_agent,
        "INSTRUCTION",
        AgentInstruction(
            role="Summarize job materials with an explicitly longer role description.",
            objective=(
                "Build the grounded report, evidence notes, and prep plan while "
                "preserving every supplied constraint."
            ),
            boundary=(
                "Use only selected sources, keep unsupported claims visible, "
                "and never fill missing experience with guesses."
            ),
        ),
    )

    assert summarize_agent.summarize_prompt_protected_tokens(
        skill_prompt,
        constraints,
        "Summarize my fit.",
    ) > original_tokens
