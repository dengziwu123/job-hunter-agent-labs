from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MultiAgentConfig:
    max_turns: int
    max_tool_calls: int
    max_model_calls: int


def load_config() -> MultiAgentConfig:
    return MultiAgentConfig(max_turns=4, max_tool_calls=1, max_model_calls=3)
