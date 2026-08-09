from __future__ import annotations

import json
from typing import Any

from labs.shared.config import Settings, load_settings
from labs.shared.providers import (
    ProviderToolCall,
    complete_messages,
    complete_tool_result,
    request_tool_call as request_provider_tool_call,
)
from labs.lab_03.src.tools import SourceResult


# parameters uses the SDK/OpenAPI schema form (google.genai types.Schema),
# which is what FunctionDeclaration documents — not standard JSON Schema.
SEARCH_SOURCES_DECLARATION = {
    "name": "search_sources",
    "description": "Search the local job-hunting sources for evidence snippets relevant to a query.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "What evidence to look for, e.g. skills or requirements to match.",
            },
            "limit": {
                "type": "INTEGER",
                "description": "Maximum number of results to return (1-3).",
            },
        },
        "required": ["query"],
    },
}

DEFAULT_TOOL_REQUEST: dict[str, Any] = {"query": "Python LLM API experience", "limit": 2}
OFFLINE_DRAFT = (
    "Hello, I noticed the AI Tools Engineer opening. My Python API integration "
    "experience matches the automation needs of the role, and I can share "
    "supported project examples on request."
)


class ToolUseModel:
    """Course-provided plumbing that lets the selected model drive Lab 3 tools.

    Live mode (selected provider API key set): the model decides how to call
    `search_sources`, then drafts the outreach text from the tool results.
    Offline mode: a deterministic stub keeps the demo and CI runnable.

    Call `request_tool_call()` first, execute the tool yourself, then call
    `draft_outreach()` with the results.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.calls = 0
        # "model" when the tool request came from a real provider function call,
        # "fallback" when the deterministic default was used instead.
        self.tool_call_source: str | None = None
        self._provider_tool_call: ProviderToolCall | None = None
        self._contents: list[Any] | None = None
        self._config: Any = None
        self._model_content: Any = None
        self._function_call: Any = None
        self.tool_call_io: dict[str, Any] = {}
        self.draft_io: dict[str, Any] = {}

    @property
    def live(self) -> bool:
        return bool(self.settings.api_key)

    @property
    def mode(self) -> str:
        return "live" if self.live else "offline"

    def _client_and_types(self) -> tuple[Any, Any]:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError("google-genai is required. Run `uv sync` first.") from exc
        return genai.Client(api_key=self.settings.google_api_key), types

    def request_tool_call(self, user_request: str) -> dict[str, Any]:
        """Ask the model which search_sources call it wants to make."""
        instruction = (
            "You help with job hunting using only the current fetched Web sources. "
            "Call search_sources to gather evidence before drafting anything. "
            "Never invent candidate experience."
        )
        prompt = f"{instruction}\nUser request: {user_request}"
        if not self.live:
            self.tool_call_source = "fallback"
            request = dict(DEFAULT_TOOL_REQUEST)
            self._record_tool_call_io(
                instruction,
                user_request,
                prompt,
                {"source": "offline_fallback"},
                request,
            )
            return request

        if self.settings.provider != "gemini":
            self._provider_tool_call = request_provider_tool_call(
                self.settings,
                prompt,
                SEARCH_SOURCES_DECLARATION,
            )
            self.calls += 1
            if self._provider_tool_call is None:
                self.tool_call_source = "fallback"
                request = dict(DEFAULT_TOOL_REQUEST)
                self._record_tool_call_io(
                    instruction,
                    user_request,
                    prompt,
                    {"source": "provider", "tool_call": None},
                    request,
                )
                return request
            self.tool_call_source = "model"
            request = _bounded_tool_arguments(self._provider_tool_call.arguments)
            self._record_tool_call_io(
                instruction,
                user_request,
                prompt,
                {
                    "source": "provider",
                    "name": self._provider_tool_call.name,
                    "arguments": self._provider_tool_call.arguments,
                },
                request,
            )
            return request

        client, types = self._client_and_types()
        tool = types.Tool(function_declarations=[SEARCH_SOURCES_DECLARATION])
        self._config = types.GenerateContentConfig(tools=[tool])
        self._contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part(
                        text=prompt
                    )
                ],
            )
        ]
        response = client.models.generate_content(
            model=self.settings.model,
            contents=self._contents,
            config=self._config,
        )
        self.calls += 1
        self._model_content = response.candidates[0].content

        self._function_call = None
        for part in self._model_content.parts or []:
            if part.function_call and part.function_call.name == "search_sources":
                self._function_call = part.function_call
                break

        if self._function_call is None:
            self.tool_call_source = "fallback"
            request = dict(DEFAULT_TOOL_REQUEST)
            self._record_tool_call_io(
                instruction,
                user_request,
                prompt,
                {"source": "gemini", "tool_call": None},
                request,
            )
            return request

        self.tool_call_source = "model"
        raw_arguments = dict(self._function_call.args or {})
        request = _bounded_tool_arguments(raw_arguments)
        self._record_tool_call_io(
            instruction,
            user_request,
            prompt,
            {
                "source": "gemini",
                "name": self._function_call.name,
                "arguments": raw_arguments,
            },
            request,
        )
        return request

    def draft_outreach(self, user_request: str, results: list[SourceResult]) -> str:
        """Send the tool results back and let the model draft the outreach text."""
        payload = [
            {"source_id": result.source_id, "title": result.title, "snippet": result.snippet}
            for result in results
        ]
        instruction_text = (
            "Write a short outreach email (3-4 sentences) grounded only in the "
            "tool results above. State only supported facts. Output the email text only."
        )
        if not self.live:
            self._record_draft_io(user_request, instruction_text, payload, OFFLINE_DRAFT)
            return OFFLINE_DRAFT

        if self.settings.provider != "gemini":
            if self._provider_tool_call is not None:
                text = complete_tool_result(
                    self.settings,
                    self._provider_tool_call,
                    payload,
                    instruction_text,
                )
            else:
                text = complete_messages(
                    self.settings,
                    [
                        {
                            "role": "user",
                            "content": (
                                f"User request: {user_request}\n"
                                f"search_sources results: {payload}\n"
                                f"{instruction_text}"
                            ),
                        }
                    ],
                )
            self.calls += 1
            draft = text.strip() or OFFLINE_DRAFT
            self._record_draft_io(user_request, instruction_text, payload, draft)
            return draft

        client, types = self._client_and_types()
        instruction = types.Part(text=instruction_text)
        if self._function_call is not None:
            response_part = types.Part.from_function_response(
                name="search_sources",
                response={"results": payload},
            )
            # Newer Gemini models attach an id to each function call; the
            # response must echo it so the API maps the result back correctly.
            call_id = getattr(self._function_call, "id", None)
            if call_id and response_part.function_response is not None:
                response_part.function_response.id = call_id
            self._contents.append(self._model_content)
            self._contents.append(types.Content(role="user", parts=[response_part, instruction]))
        else:
            summary = types.Part(text=f"search_sources results: {payload}")
            self._contents.append(types.Content(role="user", parts=[summary, instruction]))

        response = client.models.generate_content(
            model=self.settings.model,
            contents=self._contents,
            config=self._config,
        )
        self.calls += 1
        draft = (response.text or "").strip() or OFFLINE_DRAFT
        self._record_draft_io(user_request, instruction_text, payload, draft)
        return draft

    def _record_tool_call_io(
        self,
        instruction: str,
        user_request: str,
        prompt: str,
        raw_output: dict[str, Any],
        validated_output: dict[str, Any],
    ) -> None:
        self.tool_call_io = {
            "provider": self.settings.provider,
            "model": self.settings.model,
            "system_prompt": instruction,
            "user_request": user_request,
            "provider_input_mode": "normalized_tool_flow",
            "actual_provider_input": _json_text(
                {
                    "model": self.settings.model,
                    "contents": prompt,
                    "tools": [SEARCH_SOURCES_DECLARATION],
                }
            ),
            "raw_model_output": _json_text(raw_output),
            "validated_output": _json_text(validated_output),
        }

    def _record_draft_io(
        self,
        user_request: str,
        instruction: str,
        tool_results: list[dict[str, Any]],
        draft: str,
    ) -> None:
        self.draft_io = {
            "provider": self.settings.provider,
            "model": self.settings.model,
            "system_prompt": instruction,
            "user_request": user_request,
            "provider_input_mode": "normalized_tool_flow",
            "actual_provider_input": _json_text(
                {
                    "model": self.settings.model,
                    "tool": "search_sources",
                    "tool_results": tool_results,
                    "instruction": instruction,
                }
            ),
            "raw_model_output": draft,
        }


def _bounded_tool_arguments(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or DEFAULT_TOOL_REQUEST["query"])
    limit = int(args.get("limit") or DEFAULT_TOOL_REQUEST["limit"])
    return {"query": query, "limit": max(1, min(limit, 3))}


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)
