from __future__ import annotations

import json
from pathlib import Path

import pytest

from labs.lab_05.web_adapter import Lab05Adapter
from labs.shared.artifacts import read_json
from labs.shared.web.contracts import ArtifactLink, ChatRequest
from labs.shared.web.materials import MaterialStore


def test_candidate_artifact_keeps_only_compact_decision_checkpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import labs.lab_05.web_adapter as web_adapter

    secret_prompt = "full provider prompt that belongs only in the trace artifact"

    def fake_upstream(**_) -> dict:
        return {
            "structured_report": {},
            "task_state": {},
            "claims": [],
            "model": {
                "mode": "offline",
                "claim_source": "fixture",
                "model_io": {
                    "prompt": secret_prompt,
                    "raw_model_output": "private raw output",
                    "output_source": "offline_fixture",
                },
            },
            "skill": {},
            "mcp_boundary": {},
            "request_context": "Prepare a grounded local draft.",
            "draft_action": {},
        }

    full_trajectory = [
        {
            "sequence": 1,
            "type": "model",
            "status": "completed",
            "component": "labs.lab_04.src.claim_generation",
            "operation": "generate_claims",
            "details": {"semantic_key": "model.generate_claims", "model_io": secret_prompt},
        },
        {
            "sequence": 2,
            "type": "policy",
            "status": "completed",
            "component": "labs.lab_03.src.policies",
            "operation": "policy_checkpoint",
            "details": {
                "semantic_key": "policy.decision_checkpoint",
                "output_status": "draft_created",
                "reason": "large policy explanation",
            },
        },
        {
            "sequence": 3,
            "type": "evidence",
            "status": "completed",
            "component": "labs.lab_04.src.evidence",
            "operation": "evidence_checkpoint",
            "details": {
                "semantic_key": "evidence.decision_checkpoint",
                "output_status": "partial",
                "supporting_snippet": "do not duplicate source text",
            },
        },
        {
            "sequence": 4,
            "type": "trace",
            "status": "completed",
            "component": "labs.lab_05.src.response_boundary",
            "operation": "response_boundary",
            "details": {
                "semantic_key": "response.apply_boundary",
                "input_policy_status": "draft_created",
                "input_evidence_status": "partial",
                "output_status": "ok",
                "response_kind": "grounded_draft",
                "external_action_performed": False,
                "unsupported_claim": "do not persist this claim",
            },
        },
        {
            "sequence": 5,
            "type": "trace",
            "status": "completed",
            "component": "labs.lab_05.src.executor",
            "operation": "final_response",
            "details": {
                "semantic_key": "response.finalize",
                "output_status": "ok",
                "response_kind": "grounded_draft",
                "assistant_message": "full candidate response already stored once above",
            },
        },
    ]

    def fake_execute(_prompt, *, upstream_runner, **_) -> dict:
        upstream_runner()
        return {
            "message": "Grounded local draft.",
            "status": "ok",
            "evidence_notes": [],
            "trajectory": full_trajectory,
        }

    monkeypatch.setattr(web_adapter, "build_evidence_capability", fake_upstream)
    monkeypatch.setattr(web_adapter.executor, "execute_job_agent_case", fake_execute)
    monkeypatch.setattr(
        web_adapter,
        "persistent_artifact_path",
        lambda *parts: tmp_path.joinpath(*parts),
    )
    monkeypatch.setattr(web_adapter, "artifact_path", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(web_adapter, "relative_artifact_path", lambda path: path.as_posix())
    monkeypatch.setattr(
        web_adapter,
        "record_run_trace",
        lambda *_: ArtifactLink(label="Lab 05 call trace", path="trace.json"),
    )

    response = Lab05Adapter(MaterialStore(tmp_path / "materials")).chat(
        ChatRequest(
            stage="lab_05",
            session_id="session_candidate_artifact",
            workspace_id="workspace_candidate_artifact",
            messages=[{"role": "user", "content": "Prepare a grounded local draft."}],
        )
    )
    candidate_link = next(artifact for artifact in response.artifacts if artifact.label == "Candidate response")
    candidate = read_json(Path(candidate_link.path))

    assert [event["operation"] for event in candidate["trajectory"]] == [
        "policy_checkpoint",
        "evidence_checkpoint",
        "response_boundary",
        "final_response",
    ]
    assert all(set(event) == {"operation", "status", "details"} for event in candidate["trajectory"])
    allowed_details = {
        "semantic_key",
        "input_policy_status",
        "input_evidence_status",
        "output_status",
        "response_kind",
        "external_action_performed",
    }
    assert all(set(event["details"]) <= allowed_details for event in candidate["trajectory"])
    serialized = json.dumps(candidate)
    assert "model_io" not in serialized
    assert secret_prompt not in serialized
    assert "private raw output" not in serialized
    assert "supporting_snippet" not in serialized
    assert "assistant_message" not in serialized
