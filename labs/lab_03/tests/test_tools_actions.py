from __future__ import annotations

import json
from pathlib import Path

import labs.shared.artifacts as artifacts
import labs.shared.web.tracing as tracing
from labs.lab_01.src.model_client import ModelClient
from labs.shared.artifacts import read_json
from labs.shared.config import Settings
from labs.shared.providers import ProviderToolCall
from labs.lab_03.web_adapter import Lab03Adapter, load_task_state, stateful_request_context
from labs.shared.web.contracts import ChatRequest
from labs.shared.web.materials import MaterialStore
from labs.shared.web.web_fetch import FetchedWebPage
from labs.lab_03.src.policies import DraftAction, draft_action
from labs.lab_03.src.run_with_tools import write_trace_event
from labs.lab_03.src.tools import SourceResult, search_sources
from labs.lab_03.src.state_store import advance_task_state


def test_course_plumbing_routes_openai_tool_call_and_result(monkeypatch) -> None:
    import labs.lab_03.src.model_tool_use as model_tool_use

    provider_call = ProviderToolCall(
        name="search_sources",
        arguments={"query": "Python", "limit": 9},
        continuation={"messages": [], "tool_call_id": "call_1"},
    )
    monkeypatch.setattr(
        model_tool_use,
        "request_provider_tool_call",
        lambda settings, prompt, declaration: provider_call,
    )
    monkeypatch.setattr(
        model_tool_use,
        "complete_tool_result",
        lambda settings, call, result, instruction: "Grounded provider draft",
    )
    model = model_tool_use.ToolUseModel(
        Settings(model="test", provider="openai", openai_api_key="test-key")
    )

    request = model.request_tool_call("Draft from sources.")
    draft = model.draft_outreach(
        "Draft from sources.",
        [
            SourceResult(
                source_id="source_1",
                title="Source",
                path="source.txt",
                snippet="Python API experience.",
            )
        ],
    )

    assert request == {"query": "Python", "limit": 3}
    assert draft == "Grounded provider draft"
    assert model.tool_call_source == "model"
    assert model.calls == 2
    assert json.loads(model.tool_call_io["validated_output"]) == {
        "query": "Python",
        "limit": 3,
    }
    assert json.loads(model.draft_io["actual_provider_input"])["tool_results"][0][
        "source_id"
    ] == "source_1"
    assert model.draft_io["raw_model_output"] == "Grounded provider draft"


def test_search_sources_returns_stable_source_results() -> None:
    results = search_sources("Python LLM API", limit=2)

    assert len(results) == 2
    assert all(isinstance(result, SourceResult) for result in results)
    assert results[0].source_id
    assert results[0].snippet


def test_search_sources_accepts_current_job_workspace_sources() -> None:
    source_items = [
        {
            "source_id": "mat_profile_current",
            "title": "Current uploaded profile",
            "path": "job-material://mat_profile_current",
            "snippet": "Candidate has Python API integration experience.",
        },
        {
            "source_id": "mat_jd_current",
            "title": "Current uploaded JD",
            "path": "job-material://mat_jd_current",
            "snippet": "Role requires product analytics and SQL.",
        },
    ]

    results = search_sources("Python API", limit=1, source_items=source_items)

    assert [result.source_id for result in results] == ["mat_profile_current"]
    assert results[0].path == "job-material://mat_profile_current"


def test_safe_action_returns_draft_created() -> None:
    action = draft_action("outreach_draft", "Draft a short outreach email.")

    assert isinstance(action, DraftAction)
    assert action.status == "draft_created"


def test_managed_task_state_keeps_identity_and_advances_revision() -> None:
    report = {
        "fit_summary": "Partial fit",
        "strengths": ["Python"],
        "gaps": ["LLM operations"],
        "risks": ["Unsupported claims"],
        "missing_info": ["Production scale"],
        "recommended_next_steps": ["Collect evidence"],
    }

    first = advance_task_state(
        None,
        user_request="First request",
        report=report,
        source_ids=["source_1"],
        action_status="draft_created",
        run_id="run_1",
    )
    second = advance_task_state(
        first.model_dump(),
        user_request="Continue with this new evidence",
        report=report,
        source_ids=["source_2"],
        action_status="needs_approval",
        run_id="run_2",
    )

    assert first.revision == 1
    assert second.state_id == first.state_id
    assert second.revision == 2
    assert second.user_requests == ["First request", "Continue with this new evidence"]
    assert second.source_ids == ["source_2"]

    current = second
    for index in range(3, 25):
        current = advance_task_state(
            current.model_dump(),
            user_request=f"Request {index}",
            report=report,
            source_ids=[f"source_{index}"],
            action_status="draft_created",
            run_id=f"run_{index}",
        )

    assert len(current.user_requests) == 20
    assert current.user_requests[0] == "Request 5"
    assert current.user_requests[-1] == "Request 24"


def test_load_task_state_discards_an_incompatible_persisted_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(artifacts, "ROOT_DIR", tmp_path)
    request = ChatRequest(
        stage="lab_03",
        session_id="session_lab03invalid",
        workspace_id="workspace_lab03invalid",
        messages=[{"role": "user", "content": "Continue."}],
    )
    state_path = artifacts.task_state_path("lab_03", request.workspace_id)
    artifacts.write_json(state_path, {"state_id": "missing_revision"})

    state, discarded = load_task_state(request, "lab_03")

    assert state is None
    assert discarded is True


def test_stateful_context_is_bounded_and_does_not_reinject_prior_model_output() -> None:
    prior_report_marker = "PRIOR_REPORT_MUST_NOT_BE_REINJECTED"
    assistant_marker = "ASSISTANT_RESPONSE_MUST_NOT_BE_REINJECTED"
    previous_state = {
        "state_id": "state_test",
        "revision": 2,
        "user_requests": ["Old request"],
        "report": {
            "fit_summary": prior_report_marker,
            "strengths": ["Python"],
            "gaps": ["LLM operations"],
            "risks": ["Unsupported claims"],
            "missing_info": ["Production scale"],
            "recommended_next_steps": ["Collect evidence"],
        },
        "source_ids": ["source_1"],
        "action_status": "draft_created",
        "latest_run_id": "run_2",
        "validation_status": "valid",
    }
    request = ChatRequest(
        stage="lab_03",
        session_id="session_lab03context",
        workspace_id="workspace_lab03context",
        messages=[
            {"role": "user", "content": "Use my existing Python evidence."},
            {"role": "assistant", "content": assistant_marker},
            {"role": "user", "content": "Now include the migration example."},
        ],
    )

    context = stateful_request_context(request, previous_state)

    assert prior_report_marker not in context
    assert assistant_marker not in context
    assert "Use my existing Python evidence." in context
    assert "Now include the migration example." in context
    assert '"revision": 2' in context


def test_unsafe_prompt_statuses_match_fixture() -> None:
    fixture_path = Path(__file__).resolve().parents[1] / "data" / "unsafe_prompts.json"
    prompts = read_json(fixture_path)

    for prompt in prompts:
        action = draft_action(prompt["action_type"], prompt["content"])
        assert action.status == prompt["expected_status"]


def test_trace_event_shape_is_jsonl_safe(tmp_path: Path, monkeypatch) -> None:
    import labs.shared.artifacts as artifacts
    import labs.lab_03.src.run_with_tools as run_with_tools

    def fake_artifact_path(*parts: str) -> Path:
        path = tmp_path / Path(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(artifacts, "artifact_path", fake_artifact_path)
    monkeypatch.setattr(run_with_tools, "artifact_path", fake_artifact_path)

    write_trace_event({"event": "draft_action", "status": "needs_approval"})
    write_trace_event({"event": "approval_decision", "status": "needs_approval"})

    trace_path = tmp_path / "lab_03" / "tool_trace.jsonl"
    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"event": "draft_action", "status": "needs_approval"}
    assert json.loads(lines[1]) == {"event": "approval_decision", "status": "needs_approval"}


def test_search_sources_declaration_builds_a_valid_sdk_tool() -> None:
    from google.genai import types
    from labs.lab_03.src.model_tool_use import SEARCH_SOURCES_DECLARATION

    tool = types.Tool(function_declarations=[SEARCH_SOURCES_DECLARATION])
    declaration = tool.function_declarations[0]

    assert declaration.name == "search_sources"
    assert declaration.parameters.type == types.Type.OBJECT
    assert declaration.parameters.required == ["query"]
    assert declaration.parameters.properties["limit"].type == types.Type.INTEGER


class FakeToolUseModel:
    """Offline stand-in for the Gemini tool-use client. Tests never call the API."""

    mode = "fake"
    calls = 0
    tool_call_source: str | None = None

    def request_tool_call(self, user_request: str) -> dict:
        self.tool_call_source = "model"
        return {"query": "Python LLM API", "limit": 2}

    def draft_outreach(self, user_request: str, results: list) -> str:
        assert results, "draft_outreach should receive the executed tool results"
        return "Sample outreach draft grounded in tool results."


def test_run_demo_routes_model_tool_call_through_policy(tmp_path: Path, monkeypatch) -> None:
    import labs.shared.artifacts as artifacts
    import labs.lab_03.src.run_with_tools as run_with_tools

    def fake_artifact_path(*parts: str) -> Path:
        path = tmp_path / Path(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(artifacts, "artifact_path", fake_artifact_path)
    monkeypatch.setattr(run_with_tools, "artifact_path", fake_artifact_path)

    run_with_tools.run_demo(model=FakeToolUseModel())

    trace_path = tmp_path / "lab_03" / "tool_trace.jsonl"
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    event_types = [event["event"] for event in events]

    assert event_types == ["tool_call", "tool_result", "draft_action", "draft_action", "approval_decision"]
    assert events[0]["input"] == {"query": "Python LLM API", "limit": 2}
    assert events[0]["source"] == "model"
    assert events[2]["content"] == "Sample outreach draft grounded in tool results."
    assert events[2]["status"] == "draft_created"
    assert events[4]["status"] == "needs_approval"


def test_lab_3_web_adapter_runs_complete_stage_without_lab_2_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = MaterialStore(tmp_path / "materials")
    workspace_id = "workspace_lab03test"
    store.create_text(
        workspace_id,
        "candidate_profile",
        "profile.md",
        "Backend engineer with Python API integration experience.",
        source="fixture",
    )
    store.create_text(
        workspace_id,
        "job_description",
        "jd.md",
        "AI Tools Engineer role requiring Python and LLM API experience.",
        source="fixture",
    )
    queued = store.create_url(workspace_id, "https://example.com/northstar/engineering")
    monkeypatch.setattr(
        "labs.shared.web.web_fetch.fetch_web_page",
        lambda url: FetchedWebPage(
            url=url,
            title="Northstar Engineering",
            text="Northstar builds workflow automation tools with Python APIs.",
        ),
    )
    monkeypatch.setattr(
        ModelClient,
        "complete",
        lambda _self, _messages: json.dumps(
            {
                "fit_summary": "Partial fit",
                "strengths": ["Python"],
                "gaps": ["Production LLM evidence"],
                "risks": ["Unsupported claims"],
                "missing_info": ["Production scale"],
                "recommended_next_steps": ["Collect evidence"],
            }
        ),
    )
    monkeypatch.setattr(
        "labs.lab_03.src.model_tool_use.load_settings",
        lambda: Settings(model="offline-test", google_api_key=""),
    )

    response = Lab03Adapter(store).chat(
        ChatRequest(
            stage="lab_03",
            session_id="session_lab03test",
            workspace_id=workspace_id,
            messages=[
                {
                    "role": "user",
                    "content": "Search current sources, draft an outreach email, and send it now.",
                }
            ],
        )
    )

    assert response.status == "ok"
    assert response.state_summary["draft_action"]["status"] == "needs_approval"
    assert [event.type for event in response.events] == [
        "state_load",
        "model_call",
        "validation",
        "state_update",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "model_call",
        "guardrail",
        "state_update",
    ]
    assert response.events[1].component == "labs.lab_01.src.model_client.ModelClient"
    assert response.events[2].operation == "validate_fit_gap_report"
    assert response.events[4].component == "labs.shared.web.web_fetch"
    assert response.events[4].operation == "fetch_web_page"
    assert response.events[5].details["material_id"] == queued.material_id
    assert response.events[6].details["provider_input_mode"] == "normalized_tool_flow"
    assert response.events[7].details["source_ids"] == [queued.material_id]
    assert response.events[8].details["raw_model_output"] in response.assistant_message
    assert response.events[-2].operation == "classify_action"
    timed_operations = {
        event.operation: event.duration_ms
        for event in response.events
        if event.operation in {
            "request_tool_call",
            "search_sources",
            "draft_outreach",
            "classify_action",
        }
    }
    assert timed_operations.keys() == {
        "request_tool_call",
        "search_sources",
        "draft_outreach",
        "classify_action",
    }
    assert all(duration is not None and duration > 0 for duration in timed_operations.values())
    assert response.events[-1].operation == "advance_task_state"
    assert response.state_summary["task_state"]["revision"] == 1
    assert store.list(workspace_id)[-1].status == "ready"


def test_lab_3_web_adapter_resumes_conversation_and_persisted_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(artifacts, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(tracing, "ROOT_DIR", tmp_path)
    monkeypatch.setattr("labs.lab_02.src.state_store.ROOT_DIR", tmp_path, raising=False)
    store = MaterialStore(tmp_path / "materials")
    workspace_id = "workspace_lab03state"
    store.create_text(
        workspace_id,
        "candidate_profile",
        "profile.md",
        "Backend engineer with Python API integration experience.",
        source="fixture",
    )
    store.create_text(
        workspace_id,
        "job_description",
        "jd.md",
        "AI Tools Engineer role requiring Python and LLM API experience.",
        source="fixture",
    )
    model_prompts: list[str] = []

    def fake_complete(_self, messages):
        model_prompts.append("\n".join(message["content"] for message in messages))
        return json.dumps(
            {
                "fit_summary": "Partial fit",
                "strengths": ["Python"],
                "gaps": ["Production LLM evidence"],
                "risks": ["Unsupported claims"],
                "missing_info": ["Production scale"],
                "recommended_next_steps": ["Collect evidence"],
            }
        )

    monkeypatch.setattr(ModelClient, "complete", fake_complete)
    monkeypatch.setattr(
        "labs.lab_03.src.model_tool_use.load_settings",
        lambda: Settings(model="offline-test", google_api_key=""),
    )
    adapter = Lab03Adapter(store)
    first = adapter.chat(
        ChatRequest(
            stage="lab_03",
            session_id="session_lab03state",
            workspace_id=workspace_id,
            messages=[{"role": "user", "content": "Draft from my current evidence."}],
        )
    )
    second = adapter.chat(
        ChatRequest(
            stage="lab_03",
            session_id="session_lab03state",
            workspace_id=workspace_id,
            messages=[
                {"role": "user", "content": "Draft from my current evidence."},
                {"role": "assistant", "content": first.assistant_message},
                {"role": "user", "content": "Now incorporate my API migration example."},
            ],
        )
    )

    first_state = first.state_summary["task_state"]
    second_state = second.state_summary["task_state"]
    assert second_state["state_id"] == first_state["state_id"]
    assert first_state["revision"] == 1
    assert second_state["revision"] == 2
    first_state_artifact = next(
        artifact for artifact in first.artifacts if artifact.label == "Managed task state"
    )
    second_state_artifact = next(
        artifact for artifact in second.artifacts if artifact.label == "Managed task state"
    )
    assert first_state_artifact.path.endswith("/revisions/revision_1.json")
    assert second_state_artifact.path.endswith("/revisions/revision_2.json")
    assert second_state["user_requests"] == [
        "Draft from my current evidence.",
        "Now incorporate my API migration example.",
    ]
    assert first.assistant_message not in model_prompts[-1]
    assert "Draft from my current evidence." in model_prompts[-1]
    assert "Now incorporate my API migration example." in model_prompts[-1]
    structured_model_event = next(
        event
        for event in second.events
        if event.operation == "complete" and "user_request" in event.details
    )
    assert structured_model_event.details["user_request"] == (
        "Now incorporate my API migration example."
    )
    assert "Earlier user requests" in structured_model_event.details["actual_provider_input"]
    assert second.events[0].operation == "load_task_state"
    assert second.events[0].details["revision"] == 1
    assert artifacts.task_state_revision_path("lab_03", workspace_id, 1).is_file()
    assert artifacts.task_state_revision_path("lab_03", workspace_id, 2).is_file()
