from __future__ import annotations

import json
from typing import Any

from labs.shared.config import Settings, load_settings


class LlmSession:
    """Thin Gemini text wrapper shared by the later labs.

    Live mode calls Gemini when GOOGLE_API_KEY is set. Offline mode returns the
    caller-provided fallback text so demos, tests, and CI stay runnable without
    a key. `calls` counts consumed model-call slots in both modes so budget
    limits behave the same everywhere.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.calls = 0

    @property
    def live(self) -> bool:
        return bool(self.settings.google_api_key)

    @property
    def mode(self) -> str:
        return "live" if self.live else "offline"

    def complete(self, prompt: str, offline_text: str) -> str:
        self.calls += 1
        if not self.live:
            return offline_text
        text = self._complete_live(prompt).strip()
        return text or offline_text

    def complete_json(self, prompt: str, response_schema: dict[str, Any], offline_payload: Any) -> Any:
        """Structured JSON model call.

        `response_schema` must use the SDK/OpenAPI schema form documented for
        google-genai's response_schema config (uppercase type values such as
        "OBJECT"/"STRING"), not standard JSON Schema.

        Live mode asks Gemini for schema-shaped JSON and returns the parsed
        payload; a malformed response raises `json.JSONDecodeError` for the
        caller to handle. Offline mode returns `offline_payload`. Either way
        one call slot is consumed.
        """
        self.calls += 1
        if not self.live:
            return offline_payload
        return json.loads(self._complete_live_json(prompt, response_schema))

    def _complete_live(self, prompt: str) -> str:
        client, _ = self._client_and_types()
        response = client.models.generate_content(model=self.settings.model, contents=prompt)
        return response.text or ""

    def _complete_live_json(self, prompt: str, response_schema: dict[str, Any]) -> str:
        client, types = self._client_and_types()
        response = client.models.generate_content(
            model=self.settings.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=response_schema,
            ),
        )
        return response.text or ""

    def _client_and_types(self) -> tuple[Any, Any]:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError("google-genai is required. Run `uv sync` first.") from exc

        return genai.Client(api_key=self.settings.google_api_key), types
