from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from labs.shared.artifacts import read_json
from labs.shared.config import Settings
from labs.lab_02.web_adapter import Lab02Adapter
from labs.shared.web.contracts import ChatRequest
from labs.shared.web.materials import MaterialStore
from labs.lab_02.src.run_structured import GeminiStructuredReportClient, run
from labs.lab_02.src.schemas import CandidateProfile, FitGapReport, JobDescription, validate_fit_gap_report
from labs.lab_02.src.state_store import create_initial_state, mark_report_generated


class RecordingStructuredClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def generate_report_json(
        self,
        profile: CandidateProfile,
        job_description: JobDescription,
    ) -> dict:
        self.calls.append((profile.id, job_description.id))
        return {
            "fit_summary": f"CLIENT_SENTINEL:{profile.id}:{job_description.id}",
            "strengths": [f"strength-from-{profile.id}"],
            "gaps": [f"gap-for-{job_description.id}"],
            "risks": ["risk-from-client"],
            "missing_info": ["missing-info-from-client"],
            "recommended_next_steps": ["next-step-from-client"],
        }


class RecordingLab1ModelClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] | None = None

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.messages = messages
        return """{
          "fit_summary": "Structured through the Lab 1 model boundary.",
          "strengths": ["Python"],
          "gaps": ["LLM evaluation"],
          "risks": ["Unsupported claims"],
          "missing_info": ["Target level"],
          "recommended_next_steps": ["Collect project evidence"]
        }"""


def test_structured_client_reuses_lab_1_model_boundary() -> None:
    raw_model = RecordingLab1ModelClient()
    client = GeminiStructuredReportClient(
        Settings(model="fake-model", google_api_key="fake-key"),
        model_client=raw_model,  # type: ignore[arg-type]
    )
    profile = CandidateProfile(
        id="profile-test",
        headline="Backend engineer",
        skills=["Python"],
        projects=[],
        constraints=[],
    )
    jd = JobDescription(
        id="jd-test",
        title="AI Tools Engineer",
        company="Northstar Systems",
        requirements=["Python"],
        nice_to_have=[],
    )

    data = client.generate_report_json(profile, jd)

    assert data["fit_summary"].startswith("Structured")
    assert raw_model.messages is not None
    assert raw_model.messages[0]["role"] == "system"
    assert "required fields" in raw_model.messages[0]["content"].lower()
    assert "profile-test" in raw_model.messages[1]["content"]
    assert "jd-test" in raw_model.messages[1]["content"]


def test_fit_gap_report_accepts_required_fields() -> None:
    report = validate_fit_gap_report(
        {
            "fit_summary": "Partial fit with clear gaps.",
            "strengths": ["Python", "API integration"],
            "gaps": ["Needs stronger LLM eval example."],
            "risks": ["May overclaim agent experience."],
            "missing_info": ["Target level"],
            "recommended_next_steps": ["Collect project evidence"],
        }
    )

    assert isinstance(report, FitGapReport)
    assert report.strengths == ["Python", "API integration"]
    assert report.recommended_next_steps == ["Collect project evidence"]


def test_malformed_report_is_rejected() -> None:
    path = Path(__file__).resolve().parents[1] / "data" / "malformed_report.json"

    with pytest.raises(ValidationError):
        validate_fit_gap_report(read_json(path))


def test_state_transition_records_report_artifact() -> None:
    profile = CandidateProfile(
        id="profile-test",
        headline="Backend engineer",
        skills=["Python"],
        projects=[],
        constraints=[],
    )
    jd = JobDescription(
        id="jd-test",
        title="AI Tools Engineer",
        company="Northstar Systems",
        requirements=["Python"],
        nice_to_have=[],
    )

    state = create_initial_state(profile, jd)
    updated = mark_report_generated(state, Path("artifacts/lab_02/job_prep_report.json"))

    assert updated.status == "report_generated"
    assert updated.validation_status == "valid"
    assert updated.latest_report_artifact == "artifacts/lab_02/job_prep_report.json"


def test_run_writes_expected_artifact_shape() -> None:
    root = Path(__file__).resolve().parents[3]
    client = RecordingStructuredClient()
    payload = run(
        root / "labs" / "lab_02" / "data" / "profile.json",
        root / "labs" / "lab_02" / "data" / "job_description.json",
        client=client,
    )

    assert client.calls == [("profile-synthetic-001", "jd-synthetic-001")]
    assert set(payload) == {"profile", "job_description", "report", "state", "validation"}
    assert payload["state"]["status"] == "report_generated"
    assert payload["state"]["latest_report_artifact"] == "artifacts/lab_02/job_prep_report.json"
    assert payload["report"]["fit_summary"] == "CLIENT_SENTINEL:profile-synthetic-001:jd-synthetic-001"
    assert payload["report"]["strengths"] == ["strength-from-profile-synthetic-001"]
    assert payload["report"]["gaps"] == ["gap-for-jd-synthetic-001"]
    assert payload["report"]["risks"] == ["risk-from-client"]
    assert payload["report"]["missing_info"] == ["missing-info-from-client"]
    assert payload["report"]["recommended_next_steps"] == ["next-step-from-client"]
    assert payload["validation"] == {"status": "valid", "schema": "FitGapReport"}

    artifact_payload = read_json(root / "artifacts" / "lab_02" / "job_prep_report.json")
    assert artifact_payload == payload


def test_lab_2_web_adapter_reuses_materials_and_exposes_validation_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[3]
    store = MaterialStore(tmp_path / "materials")
    workspace_id = "workspace_lab02test"
    store.create_text(
        workspace_id,
        "candidate_profile",
        "profile.json",
        (root / "labs" / "lab_02" / "data" / "profile.json").read_text(encoding="utf-8"),
        source="fixture",
    )
    store.create_text(
        workspace_id,
        "job_description",
        "job_description.json",
        (root / "labs" / "lab_02" / "data" / "job_description.json").read_text(encoding="utf-8"),
        source="fixture",
    )

    def fake_report(self, profile, job_description) -> dict:
        return RecordingStructuredClient().generate_report_json(profile, job_description)

    monkeypatch.setattr(GeminiStructuredReportClient, "generate_report_json", fake_report)
    response = Lab02Adapter(store).chat(
        ChatRequest(
            stage="lab_02",
            session_id="session_lab02test",
            workspace_id=workspace_id,
            messages=[{"role": "user", "content": "Explain fit and gaps."}],
        )
    )

    assert response.status == "ok"
    assert response.state_summary["validation"] == {"status": "valid", "schema": "FitGapReport"}
    assert response.state_summary["task_state"]["status"] == "report_generated"
    assert [(event.type, event.component, event.operation) for event in response.events] == [
        ("model_call", "labs.lab_01.src.model_client.ModelClient", "complete"),
        ("validation", "labs.lab_02.src.schemas", "validate_fit_gap_report"),
        ("state_update", "labs.lab_02.src.state_store", "mark_report_generated"),
    ]
