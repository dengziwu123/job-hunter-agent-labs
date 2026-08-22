from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from labs.lab_02.src.schemas import FitGapReport
from labs.lab_05.evals.analyzer import analyze_trajectory
from labs.lab_05.src import executor
from labs.shared.artifacts import artifact_path, write_json
from labs.shared.config import ROOT_DIR


def load_tasks(path: Path) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            tasks.append(json.loads(line))
    return tasks


def run_eval(
    tasks_path: Path,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Run the three canonical business cases in forced fixture mode."""
    _require_completed_lab_2()
    _require_completed_lab_3()
    results: list[dict[str, Any]] = []
    for task in load_tasks(tasks_path):
        response = executor.execute_job_agent_case(
            task["input"],
            fixture={
                "claims": task.get("claims", []),
                "sources": task.get("sources", []),
                "draft_content": task.get("draft_content"),
                "tool_result": task.get("tool_result"),
                "action_type": task.get("action_type"),
                "policy_content": task.get("policy_content"),
            },
        )
        analysis = analyze_trajectory(task, response["trajectory"])
        results.append(
            {
                "task_id": task["id"],
                **analysis,
                "response": response,
            }
        )

    summary = {
        "total": len(results),
        "passed": sum(1 for item in results if item["passed"]),
        "failed": sum(1 for item in results if not item["passed"]),
        "mode": "fixture",
        "results": results,
    }
    destination = output_path or artifact_path("lab_05", "eval_summary.json")
    try:
        relative_destination = destination.relative_to(ROOT_DIR / "artifacts").as_posix()
        summary["artifact"] = f"artifacts/{relative_destination}"
    except ValueError:
        summary["artifact"] = destination.as_posix()
    write_json(destination, summary)
    return summary


def _require_completed_lab_2() -> None:
    required_fields = {
        "fit_summary",
        "strengths",
        "gaps",
        "risks",
        "missing_info",
        "recommended_next_steps",
    }
    if not required_fields.issubset(FitGapReport.model_fields):
        raise RuntimeError(
            "Complete Lab 2 first: FitGapReport is missing the fields required by Lab 5."
        )


def _require_completed_lab_3() -> None:
    contract_path = ROOT_DIR / "labs" / "lab_03" / "data" / "unsafe_prompts.json"
    for case in json.loads(contract_path.read_text(encoding="utf-8")):
        action = executor.draft_action(case["action_type"], case["content"])
        if action.status != case["expected_status"]:
            raise RuntimeError(
                "Complete Lab 3 first: the unsafe policy contract "
                f"{case['id']} expected {case['expected_status']}, observed {action.status}."
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", required=True, type=Path)
    args = parser.parse_args()

    summary = run_eval(args.tasks)
    print(f"mode={summary['mode']}")
    print(f"passed={summary['passed']}")
    print(f"failed={summary['failed']}")
    print(f"artifact={summary['artifact']}")


if __name__ == "__main__":
    main()
