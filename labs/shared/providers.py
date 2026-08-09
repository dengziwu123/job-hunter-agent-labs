from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from labs.shared.config import Settings


OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


@dataclass(frozen=True)
class ProviderToolCall:
    name: str
    arguments: dict[str, Any]
    continuation: dict[str, Any]


@dataclass(frozen=True)
class ProviderToolDecision:
    call: ProviderToolCall | None
    raw_response: dict[str, Any]


def complete_messages(settings: Settings, messages: list[dict[str, str]]) -> str:
    """Course-provided OpenAI/Anthropic message call.

    Gemini intentionally remains in Lab 1's student-owned model boundary.
    """
    _require_non_gemini_key(settings)
    request = provider_message_request(settings, messages)
    if settings.provider == "openai":
        payload = _post_openai(settings, request)
        return _openai_message_text(payload["choices"][0]["message"])
    if settings.provider == "anthropic":
        payload = _post_anthropic(settings, request)
        return _anthropic_text(payload["content"])
    raise ValueError("Gemini calls belong to the Lab 1 model boundary.")


def provider_message_request(
    settings: Settings,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    """Build the exact provider request body used for a plain message call."""
    if settings.provider == "gemini":
        return {
            "model": settings.model,
            "contents": "\n".join(
                f"{message['role']}: {message['content']}"
                for message in messages
            ),
        }
    if settings.provider == "openai":
        return {"model": settings.model, "messages": messages}
    if settings.provider == "anthropic":
        system, anthropic_messages = _anthropic_messages(messages)
        request: dict[str, Any] = {
            "model": settings.model,
            "max_tokens": 2048,
            "messages": anthropic_messages,
        }
        if system:
            request["system"] = system
        return request
    raise ValueError(f"Unsupported LLM provider: {settings.provider}")


def complete_structured(
    settings: Settings,
    prompt: str,
    response_schema: dict[str, Any],
) -> Any:
    """Return schema-shaped data from OpenAI or Anthropic via forced tool use."""
    _require_non_gemini_key(settings)
    request = provider_structured_request(settings, prompt, response_schema)

    if settings.provider == "openai":
        payload = _post_openai(settings, request)
        arguments = payload["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        return json.loads(arguments)["result"]

    if settings.provider == "anthropic":
        payload = _post_anthropic(settings, request)
        tool_use = next(block for block in payload["content"] if block["type"] == "tool_use")
        return tool_use["input"]["result"]

    raise ValueError("Gemini structured calls belong to LlmSession.")


def provider_structured_request(
    settings: Settings,
    prompt: str,
    response_schema: dict[str, Any],
) -> dict[str, Any]:
    """Build the exact provider request body used for a structured call."""
    if settings.provider == "gemini":
        return {
            "model": settings.model,
            "contents": prompt,
            "config": {
                "response_mime_type": "application/json",
                "response_schema": response_schema,
            },
        }

    wrapped_schema = {
        "type": "object",
        "properties": {"result": _standard_json_schema(response_schema)},
        "required": ["result"],
        "additionalProperties": False,
    }
    tool_name = "emit_structured_response"
    description = "Return the requested structured response."

    if settings.provider == "openai":
        return {
            "model": settings.model,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": description,
                        "parameters": wrapped_schema,
                        "strict": True,
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": tool_name}},
        }

    if settings.provider == "anthropic":
        return {
            "model": settings.model,
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [
                {
                    "name": tool_name,
                    "description": description,
                    "input_schema": wrapped_schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": tool_name},
        }

    raise ValueError(f"Unsupported LLM provider: {settings.provider}")


def request_tool_call(
    settings: Settings,
    prompt: str,
    declaration: dict[str, Any],
) -> ProviderToolCall | None:
    """Ask OpenAI or Anthropic to choose a call for one declared tool."""
    return request_tool_decision(settings, prompt, declaration).call


def provider_tool_request(
    settings: Settings,
    prompt: str,
    declaration: dict[str, Any],
) -> dict[str, Any]:
    """Build the exact provider request body used to select one tool."""
    if settings.provider == "gemini":
        return {
            "model": settings.model,
            "contents": prompt,
            "config": {"tools": [{"function_declarations": [declaration]}]},
        }

    parameters = _standard_json_schema(declaration["parameters"])
    messages = [{"role": "user", "content": prompt}]
    if settings.provider == "openai":
        return {
            "model": settings.model,
            "messages": messages,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": declaration["name"],
                        "description": declaration["description"],
                        "parameters": parameters,
                    },
                }
            ],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        }
    if settings.provider == "anthropic":
        return {
            "model": settings.model,
            "max_tokens": 1024,
            "messages": messages,
            "tools": [
                {
                    "name": declaration["name"],
                    "description": declaration["description"],
                    "input_schema": parameters,
                }
            ],
        }
    raise ValueError(f"Unsupported LLM provider: {settings.provider}")


def request_tool_decision(
    settings: Settings,
    prompt: str,
    declaration: dict[str, Any],
) -> ProviderToolDecision:
    """Return both the extracted call and the provider's untouched response."""
    _require_non_gemini_key(settings)
    request = provider_tool_request(settings, prompt, declaration)

    if settings.provider == "openai":
        payload = _post_openai(settings, request)
        message = payload["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return ProviderToolDecision(call=None, raw_response=payload)
        tool_call = next(
            (item for item in tool_calls if item["function"]["name"] == declaration["name"]),
            None,
        )
        if tool_call is None:
            return ProviderToolDecision(call=None, raw_response=payload)
        try:
            arguments = json.loads(tool_call["function"]["arguments"])
        except (json.JSONDecodeError, TypeError):
            return ProviderToolDecision(call=None, raw_response=payload)
        return ProviderToolDecision(
            call=ProviderToolCall(
                name=declaration["name"],
                arguments=arguments,
                continuation={
                    "messages": [*request["messages"], message],
                    "tool_call_id": tool_call["id"],
                    "tools": request["tools"],
                },
            ),
            raw_response=payload,
        )

    if settings.provider == "anthropic":
        payload = _post_anthropic(settings, request)
        tool_use = next(
            (
                block
                for block in payload["content"]
                if block["type"] == "tool_use" and block["name"] == declaration["name"]
            ),
            None,
        )
        if tool_use is None:
            return ProviderToolDecision(call=None, raw_response=payload)
        return ProviderToolDecision(
            call=ProviderToolCall(
                name=declaration["name"],
                arguments=tool_use["input"],
                continuation={
                    "messages": [
                        *request["messages"],
                        {"role": "assistant", "content": payload["content"]},
                    ],
                    "tool_use_id": tool_use["id"],
                    "tools": request["tools"],
                },
            ),
            raw_response=payload,
        )

    raise ValueError("Gemini tool calls belong to ToolUseModel.")


def complete_tool_result(
    settings: Settings,
    tool_call: ProviderToolCall,
    result: Any,
    instruction: str,
) -> str:
    """Send one local tool result back to OpenAI or Anthropic."""
    _require_non_gemini_key(settings)

    if settings.provider == "openai":
        messages = [
            *tool_call.continuation["messages"],
            {
                "role": "tool",
                "tool_call_id": tool_call.continuation["tool_call_id"],
                "content": json.dumps(result),
            },
            {"role": "user", "content": instruction},
        ]
        payload = _post_openai(
            settings,
            {
                "model": settings.model,
                "messages": messages,
                "tools": tool_call.continuation["tools"],
            },
        )
        return _openai_message_text(payload["choices"][0]["message"])

    if settings.provider == "anthropic":
        messages = [
            *tool_call.continuation["messages"],
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call.continuation["tool_use_id"],
                        "content": json.dumps(result),
                    },
                    {"type": "text", "text": instruction},
                ],
            },
        ]
        payload = _post_anthropic(
            settings,
            {
                "model": settings.model,
                "max_tokens": 2048,
                "messages": messages,
                "tools": tool_call.continuation["tools"],
            },
        )
        return _anthropic_text(payload["content"])

    raise ValueError("Gemini tool results belong to ToolUseModel.")


def _require_non_gemini_key(settings: Settings) -> None:
    if settings.provider == "gemini":
        return
    if not settings.api_key:
        raise RuntimeError(
            f"{settings.api_key_env_var} is required for the {settings.provider.title()} API call."
        )


def _post_openai(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    response = httpx.post(
        OPENAI_CHAT_URL,
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def _post_anthropic(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    response = httpx.post(
        ANTHROPIC_MESSAGES_URL,
        headers={
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def _anthropic_messages(
    messages: list[dict[str, str]],
) -> tuple[str, list[dict[str, str]]]:
    system = "\n\n".join(message["content"] for message in messages if message["role"] == "system")
    conversation = [message for message in messages if message["role"] != "system"]
    return system, conversation


def _openai_message_text(message: dict[str, Any]) -> str:
    content = message.get("content") or ""
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if part.get("type") == "text")


def _anthropic_text(content: list[dict[str, Any]]) -> str:
    return "".join(block.get("text", "") for block in content if block.get("type") == "text")


def _standard_json_schema(value: Any) -> Any:
    if isinstance(value, dict):
        normalized = {key: _standard_json_schema(item) for key, item in value.items()}
        if isinstance(normalized.get("type"), str):
            normalized["type"] = normalized["type"].lower()
        if normalized.get("type") == "object":
            normalized.setdefault("additionalProperties", False)
        return normalized
    if isinstance(value, list):
        return [_standard_json_schema(item) for item in value]
    return value
