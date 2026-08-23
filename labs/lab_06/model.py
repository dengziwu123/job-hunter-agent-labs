from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from labs.lab_01.src.model_client import ModelClient
from labs.lab_06.contracts import AgentInstruction, validate_instruction
from labs.lab_06.errors import BudgetExceeded
from labs.shared.config import load_settings


_calls: ContextVar[int] = ContextVar("lab_06_model_calls", default=0)
_max_model_calls: ContextVar[int | None] = ContextVar("lab_06_max_model_calls", default=None)
_trace_writer: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar(
    "lab_06_model_trace_writer",
    default=None,
)
BASE_SYSTEM_INSTRUCTION = "You are one bounded role inside a job-agent harness."


def reset_session(max_model_calls: int | None = None) -> None:
    _calls.set(0)
    _max_model_calls.set(max_model_calls)


def mode() -> str:
    return "live" if load_settings().api_key else "offline"


def calls_used() -> int:
    return _calls.get()


def set_trace_writer(writer: Callable[[dict[str, Any]], None]):
    return _trace_writer.set(writer)


def reset_trace_writer(token) -> None:
    _trace_writer.reset(token)


def render_system_prompt(instruction: AgentInstruction) -> str:
    validate_instruction(instruction)
    return f"{BASE_SYSTEM_INSTRUCTION}\n\n{instruction.render()}"


def complete(
    prompt: str,
    offline_text: str,
    *,
    instruction: AgentInstruction,
) -> str:
    """Reuse the Lab 1 model boundary while enforcing the Lab 6 call budget."""
    system_prompt = render_system_prompt(instruction)
    calls = _calls.get()
    max_model_calls = _max_model_calls.get()
    if max_model_calls is not None and calls >= max_model_calls:
        raise BudgetExceeded(
            "Lab 6 max_model_calls budget exceeded before the model call was made.",
            stop_reason="model_call_budget_exceeded",
        )
    _calls.set(calls + 1)
    settings = load_settings()
    if not settings.api_key:
        return offline_text
    client = ModelClient(settings)
    return client.complete(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
    )


def traced_complete(
    *,
    agent: str,
    operation: str,
    semantic_key: str,
    prompt: str,
    offline_text: str,
    instruction: AgentInstruction,
) -> str:
    """Run one exact model boundary and trace both success and failure."""
    writer = _trace_writer.get()
    model_input = {
        "system_prompt": render_system_prompt(instruction),
        "user_prompt": "[redacted]",
    }
    try:
        result = complete(
            prompt,
            offline_text=offline_text,
            instruction=instruction,
        )
    except Exception as exc:
        if writer is not None:
            writer(
                {
                    "event": "model_call",
                    "agent": agent,
                    "component": f"labs.lab_06.agents.{agent}_agent",
                    "operation": operation,
                    "semantic_key": semantic_key,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "model_calls": calls_used(),
                    "model_input": model_input,
                }
            )
        raise
    if writer is not None:
        writer(
            {
                "event": "model_call",
                "agent": agent,
                "component": f"labs.lab_06.agents.{agent}_agent",
                "operation": operation,
                "semantic_key": semantic_key,
                "status": "completed",
                "model_calls": calls_used(),
                "model_input": model_input,
            }
        )
    return result
