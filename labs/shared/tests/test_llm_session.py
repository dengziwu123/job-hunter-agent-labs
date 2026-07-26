from __future__ import annotations

from labs.shared.config import Settings
from labs.shared.llm import LlmSession


def offline_session() -> LlmSession:
    return LlmSession(Settings(model="gemini-flash-latest", google_api_key=""))


def test_offline_session_returns_fallback_and_counts_calls() -> None:
    session = offline_session()

    assert session.mode == "offline"
    assert not session.live

    first = session.complete("Summarize the fit.", offline_text="Offline summary.")
    second = session.complete("Draft outreach.", offline_text="Offline draft.")

    assert first == "Offline summary."
    assert second == "Offline draft."
    assert session.calls == 2


def test_live_session_reports_live_mode_without_calling_api() -> None:
    session = LlmSession(Settings(model="gemini-flash-latest", google_api_key="test-key"))

    assert session.live
    assert session.mode == "live"
    assert session.calls == 0


def test_offline_complete_json_returns_payload_and_counts_calls() -> None:
    session = offline_session()
    # SDK/OpenAPI schema form, as complete_json documents.
    schema = {"type": "OBJECT", "properties": {"status": {"type": "STRING"}}}

    payload = session.complete_json("Classify this.", schema, offline_payload={"status": "ok"})

    assert payload == {"status": "ok"}
    assert session.calls == 1
