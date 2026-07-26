from __future__ import annotations

from time import perf_counter
from typing import Any

from labs.shared.config import Settings


class ModelClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.last_metadata: dict[str, Any] = {}

    def complete(self, messages: list[dict[str, str]]) -> str:
        started_at = perf_counter()
        text = self._complete_live(messages)

        elapsed_ms = int((perf_counter() - started_at) * 1000)
        self.last_metadata = {
            "model": self.settings.model,
            "latency_ms": elapsed_ms,
            "estimated_tokens": estimate_tokens(messages, text),
        }
        return text

    def _complete_live(self, messages: list[dict[str, str]]) -> str:
        if not self.settings.google_api_key:
            raise RuntimeError("GOOGLE_API_KEY is required for the Gemini API call.")

        from google import genai

        client = genai.Client(api_key=self.settings.google_api_key)
        contents = "\n\n".join(
            f"{message['role']}: {message['content']}" for message in messages
        )
        response = client.models.generate_content(
            model=self.settings.model,
            contents=contents,
        )

        if not response.text:
            raise RuntimeError("Gemini returned no assistant text.")

        return response.text


def estimate_tokens(messages: list[dict[str, str]], response_text: str) -> int:
    raw_text = " ".join(message.get("content", "") for message in messages) + " " + response_text
    return max(1, len(raw_text.split()))
