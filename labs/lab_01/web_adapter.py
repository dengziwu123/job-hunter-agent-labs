from __future__ import annotations

import uuid
from dataclasses import replace
from time import perf_counter

from labs.shared.config import load_settings
from labs.shared.providers import complete_messages
from labs.shared.web.contracts import ChatRequest, ChatResponse, HarnessEvent
from labs.shared.web.errors import StageExecutionError
from labs.shared.web.materials import MaterialStore
from labs.shared.web.model_io import model_io_details
from labs.shared.web.tracing import write_run_trace


class Lab01Adapter:
    stage_id = "lab_01"

    def __init__(self, materials: MaterialStore) -> None:
        self.materials = materials

    def chat(self, request: ChatRequest) -> ChatResponse:
        # Import at request time. A student SyntaxError/TODO must not stop the UI shell.
        from labs.lab_01.src.model_client import ModelClient

        run_id = f"run_{uuid.uuid4().hex}"
        # Web research is intentionally unavailable before Lab 3. Hidden web
        # sources must not leak into the raw-model baseline.
        records = [
            record
            for record in self.materials.context(request.workspace_id)
            if record["kind"] in {"candidate_profile", "job_description"}
        ]
        messages = build_model_messages(request, records)
        user_request = messages[-1]["content"]
        settings = load_settings()
        client = ModelClient(settings)
        provider_bridge = uses_course_provider_compatibility_bridge(client, settings)
        provider_route = (
            "course_provider_compatibility_bridge"
            if provider_bridge
            else (
                "student_gemini_model_client"
                if settings.provider == "gemini"
                else "course_provider_plumbing_in_model_client"
            )
        )
        started = perf_counter()

        try:
            assistant_text = complete_model_messages(client, settings, messages)
        except Exception as exc:
            duration_ms = int((perf_counter() - started) * 1000)
            events = [
                HarnessEvent(
                    sequence=1,
                    type="model_call",
                    status="failed",
                    component=(
                        "labs.shared.providers.complete_messages"
                        if provider_bridge
                        else "labs.lab_01.src.model_client.ModelClient"
                    ),
                    operation="complete",
                    summary=f"Provider call failed with {type(exc).__name__}",
                    duration_ms=duration_ms,
                    details={
                        "model": settings.model,
                        "provider_route": provider_route,
                        "message_count": len(messages),
                        "roles": [message["role"] for message in messages],
                        "material_ids": [record["material_id"] for record in records],
                        **model_io_details(settings, messages, user_request),
                    },
                )
            ]
            trace = write_run_trace(self.stage_id, run_id, events)
            raise StageExecutionError(exc, events, [trace], run_id) from exc

        duration_ms = int((perf_counter() - started) * 1000)
        client_metadata = getattr(client, "last_metadata", {})
        estimated_tokens = client_metadata.get("estimated_tokens")
        if estimated_tokens is None:
            estimated_tokens = estimate_tokens(messages, assistant_text)
        events = [
            HarnessEvent(
                sequence=1,
                type="model_call",
                status="completed",
                component=(
                    "labs.shared.providers.complete_messages"
                    if provider_bridge
                    else "labs.lab_01.src.model_client.ModelClient"
                ),
                operation="complete",
                summary=(
                    "Called the course provider compatibility bridge"
                    if provider_bridge
                    else (
                        "Called the student's raw Gemini model boundary"
                        if settings.provider == "gemini"
                        else "Called the Lab 1 model boundary with course provider plumbing"
                    )
                ),
                duration_ms=duration_ms,
                details={
                    "implementation": (
                        "labs/shared/providers.py"
                        if provider_bridge
                        else "labs/lab_01/src/model_client.py"
                    ),
                    "provider_route": provider_route,
                    "model": client_metadata.get("model", settings.model),
                    "message_count": len(messages),
                    "roles": [message["role"] for message in messages],
                    "material_ids": [record["material_id"] for record in records],
                    "response_characters": len(assistant_text),
                    "estimated_tokens": estimated_tokens,
                    **model_io_details(
                        replace(
                            settings,
                            model=client_metadata.get("model", settings.model),
                        ),
                        messages,
                        user_request,
                        assistant_text,
                    ),
                },
            )
        ]
        trace = write_run_trace(self.stage_id, run_id, events)
        return ChatResponse(
            status="ok",
            stage=self.stage_id,
            run_id=run_id,
            assistant_message=assistant_text,
            events=events,
            state_summary={
                "mode": "raw_context",
                "structured_state": False,
                "materials": [
                    {
                        "material_id": record["material_id"],
                        "kind": record["kind"],
                        "display_name": record["display_name"],
                    }
                    for record in records
                ],
                "limitations": [
                    "No schema validation",
                    "No durable task state",
                    "No claim-level evidence",
                    "No code-enforced action policy",
                ],
            },
            artifacts=[trace],
        )

    def run_eval(self, _: str) -> None:
        raise NotImplementedError("Lab 1 has no eval suite. Eval is introduced in Lab 5.")


def complete_model_messages(client, settings, messages: list[dict[str, str]]) -> str:
    """Keep Gemini student-owned while course plumbing handles other providers."""
    if not uses_course_provider_compatibility_bridge(client, settings):
        return client.complete(messages)
    return complete_messages(settings, messages)


def uses_course_provider_compatibility_bridge(client, settings) -> bool:
    if settings.provider == "gemini":
        return False
    if getattr(client, "supports_non_gemini_providers", False):
        return False
    for client_type in type(client).__mro__:
        for method_name in ("complete", "_complete_live"):
            method = client_type.__dict__.get(method_name)
            code = getattr(method, "__code__", None)
            namespace = getattr(method, "__globals__", {})
            if code is None:
                continue
            for name in code.co_names:
                value = namespace.get(name)
                if value is complete_messages:
                    return False
                if getattr(value, "complete_messages", None) is complete_messages:
                    return False
    return True


def estimate_tokens(messages: list[dict[str, str]], response_text: str) -> int:
    # Keep this patch-safe fallback local: legacy student src/model_client.py files
    # may not expose estimate_tokens, and the patch must never replace student src/.
    raw_text = " ".join(message.get("content", "") for message in messages) + " " + response_text
    return max(1, len(raw_text.split()))


def build_model_messages(request: ChatRequest, records: list[dict]) -> list[dict[str, str]]:
    material_blocks = []
    for record in records:
        material_blocks.append(
            "\n".join(
                [
                    f"[{record['kind']} | {record['material_id']} | {record['display_name']}]",
                    record["text"],
                    f"[end {record['material_id']}]",
                ]
            )
        )
    raw_context = "\n\n".join(material_blocks) or "No job materials are attached."
    system_message = {
        "role": "system",
        "content": (
            "You are a helpful job hunting assistant. The following local materials are raw context. "
            "Answer the user's request.\n\n" + raw_context
        ),
    }
    current_request = next(
        message
        for message in reversed(request.messages)
        if message.role == "user"
    )
    return [system_message, current_request.model_dump()]
