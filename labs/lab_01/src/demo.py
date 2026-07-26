from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from labs.shared.artifacts import artifact_path, write_json
from labs.shared.config import load_settings
from labs.lab_01.src.model_client import ModelClient


FAILURE_TYPES = {
    "bad_format",
    "missing_context",
    "unsupported_claim",
    "unsafe_action",
    "vague_answer",
    "privacy_risk",
}


def load_tasks(path: Path) -> list[dict[str, Any]]:
    tasks = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(tasks, list):
        raise ValueError("Baseline tasks file must contain a JSON list.")
    return tasks


def run(task_path: Path) -> list[dict[str, Any]]:
    settings = load_settings()
    client = ModelClient(settings)
    observations: list[dict[str, Any]] = []

    for task in load_tasks(task_path):
        messages = [
            {
                "role": "system",
                "content": "You are a cautious job hunting assistant. Do not invent experience.",
            },
            {"role": "user", "content": task["input"]},
        ]
        response = client.complete(messages)
        observation = build_observation_record(task, response, client.last_metadata)
        observations.append(observation)

        print(f"model={client.last_metadata['model']}")
        print(f"latency_ms={client.last_metadata['latency_ms']}")
        print(f"estimated_tokens={client.last_metadata['estimated_tokens']}")
        print(f"task_id={task['id']}")
        print(f"assistant_response={response}")
        print("---")

    output_path = artifact_path("lab_01", "baseline_observations.json")
    write_json(output_path, observations)
    print(f"artifact={output_path.relative_to(Path.cwd()) if output_path.is_relative_to(Path.cwd()) else output_path}")
    return observations


def build_observation_record(
    task: dict[str, Any],
    response: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    risk_type = task.get("expected_risk_type", "missing_context")
    if risk_type not in FAILURE_TYPES:
        risk_type = "missing_context"

    return {
        "task_id": task["id"],
        "expected": task["expected_behavior"],
        "model_response": response,
        "expected_risk_type": risk_type,
        "student_note": "",
        "why_it_matters": task["why_it_matters"],
        "metadata": metadata,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, type=Path)
    args = parser.parse_args()
    run(args.task)


if __name__ == "__main__":
    main()
