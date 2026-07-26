from __future__ import annotations

import json

from labs.shared.config import Settings
from labs.shared.providers import provider_message_request


def model_io_details(
    settings: Settings,
    messages: list[dict[str, str]],
    user_request: str,
    raw_model_output: str | None = None,
) -> dict[str, str]:
    system_prompt = "\n\n".join(
        message["content"]
        for message in messages
        if message["role"] == "system"
    )
    details = {
        "provider": settings.provider,
        "model": settings.model,
        "system_prompt": system_prompt,
        "user_request": user_request,
        "provider_input_mode": (
            "reconstructed_lab_1_boundary"
            if settings.provider == "gemini"
            else "actual"
        ),
        "actual_provider_input": json.dumps(
            provider_message_request(settings, messages),
            ensure_ascii=False,
            indent=2,
        ),
    }
    if raw_model_output is not None:
        details["raw_model_output"] = raw_model_output
    return details
