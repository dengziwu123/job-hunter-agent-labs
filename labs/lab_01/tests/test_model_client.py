from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace
from pathlib import Path

import pytest

from labs.shared.config import Settings
from labs.lab_01.src.demo import FAILURE_TYPES, build_observation_record, load_tasks
from labs.lab_01.src.model_client import ModelClient


class FakeModelClient(ModelClient):
    def _complete_live(self, messages: list[dict[str, str]]) -> str:
        return "Fake Gemini response"


def test_model_client_records_metadata_without_calling_api() -> None:
    client = ModelClient(
        Settings(
            model="gemini-flash-latest",
            google_api_key="",
        )
    )
    fake_client = FakeModelClient(client.settings)

    text = fake_client.complete([{"role": "user", "content": "Summarize fit and gaps."}])

    assert isinstance(text, str)
    assert text
    assert fake_client.last_metadata["model"] == "gemini-flash-latest"
    assert fake_client.last_metadata["estimated_tokens"] > 0


def test_live_model_client_requires_api_key() -> None:
    client = ModelClient(
        Settings(
            model="gemini-flash-latest",
            google_api_key="",
        )
    )

    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        client.complete([{"role": "user", "content": "Summarize fit and gaps."}])


def test_live_model_client_calls_gemini_with_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class FakeModels:
        def generate_content(self, *, model: str, contents):
            captured["model"] = model
            captured["contents"] = contents
            return SimpleNamespace(text="Behavior-tested Gemini response")

    class FakeClient:
        def __init__(self, *, api_key: str):
            captured["api_key"] = api_key
            self.models = FakeModels()

    fake_google = ModuleType("google")
    fake_google.genai = SimpleNamespace(Client=FakeClient)
    monkeypatch.setitem(sys.modules, "google", fake_google)

    client = ModelClient(Settings(model="gemini-test-model", google_api_key="test-key"))
    messages = [
        {"role": "system", "content": "Use the supplied job context."},
        {"role": "user", "content": "Summarize fit and gaps."},
    ]

    text = client._complete_live(messages)

    assert text == "Behavior-tested Gemini response"
    assert captured["api_key"] == "test-key"
    assert captured["model"] == "gemini-test-model"
    if isinstance(captured["contents"], str):
        assert "system" in captured["contents"]
        assert "Use the supplied job context." in captured["contents"]
        assert "user" in captured["contents"]
        assert "Summarize fit and gaps." in captured["contents"]
    else:
        assert captured["contents"] == messages


def test_baseline_tasks_have_required_fields() -> None:
    path = Path(__file__).resolve().parents[1] / "data" / "baseline_tasks.json"
    tasks = load_tasks(path)

    assert len(tasks) >= 5
    for task in tasks:
        assert task["id"]
        assert task["input"]
        assert task["expected_behavior"]
        assert task["why_it_matters"]
        assert task["expected_risk_type"] in FAILURE_TYPES


def test_observation_record_shape_is_stable() -> None:
    task = {
        "id": "job-baseline-test",
        "expected_behavior": "Draft only.",
        "expected_risk_type": "unsafe_action",
        "why_it_matters": "Actions need approval.",
    }

    record = build_observation_record(
        task,
        "Draft response",
        {"model": "gemini-flash-latest", "latency_ms": 1, "estimated_tokens": 3},
    )

    assert set(record) == {
        "task_id",
        "expected",
        "model_response",
        "expected_risk_type",
        "student_note",
        "why_it_matters",
        "metadata",
    }
    json.dumps(record)
