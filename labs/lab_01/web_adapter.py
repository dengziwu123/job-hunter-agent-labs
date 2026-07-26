from __future__ import annotations

import uuid
from time import perf_counter

from labs.shared.config import load_settings
from labs.shared.web.contracts import ChatRequest, ChatResponse, HarnessEvent
from labs.shared.web.errors import StageExecutionError
from labs.shared.web.materials import MaterialStore
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
        settings = load_settings()
        client = ModelClient(settings)
        started = perf_counter()

        try:
            assistant_text = client.complete(messages)
        except Exception as exc:
            duration_ms = int((perf_counter() - started) * 1000)
            events = [
                HarnessEvent(
                    sequence=1,
                    type="model_call",
                    status="failed",
                    component="labs.lab_01.src.model_client.ModelClient",
                    operation="complete",
                    summary=f"ModelClient.complete failed with {type(exc).__name__}",
                    duration_ms=duration_ms,
                    details={
                        "model": settings.model,
                        "message_count": len(messages),
                        "roles": [message["role"] for message in messages],
                        "material_ids": [record["material_id"] for record in records],
                    },
                )
            ]
            trace = write_run_trace(self.stage_id, run_id, events)
            raise StageExecutionError(exc, events, [trace], run_id) from exc

        duration_ms = int((perf_counter() - started) * 1000)
        events = [
            HarnessEvent(
                sequence=1,
                type="model_call",
                status="completed",
                component="labs.lab_01.src.model_client.ModelClient",
                operation="complete",
                summary="Called the student's raw Gemini model boundary",
                duration_ms=duration_ms,
                details={
                    "implementation": "labs/lab_01/src/model_client.py",
                    "model": client.last_metadata.get("model", settings.model),
                    "message_count": len(messages),
                    "roles": [message["role"] for message in messages],
                    "material_ids": [record["material_id"] for record in records],
                    "response_characters": len(assistant_text),
                    "estimated_tokens": client.last_metadata.get("estimated_tokens"),
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
    return [system_message, *[message.model_dump() for message in request.messages]]
