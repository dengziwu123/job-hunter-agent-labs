from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from labs.shared.artifacts import persistent_artifact_path, relative_artifact_path, write_json
from labs.shared.llm import LlmSession


JUDGE_VERDICTS = ["pass", "fail", "unknown"]
JUDGE_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "verdict": {"type": "STRING", "enum": JUDGE_VERDICTS},
        "critique": {"type": "STRING"},
    },
    "required": ["verdict", "critique"],
}
JUDGE_RUBRIC = (
    "Judge only the soft communication quality of the final candidate response: "
    "clarity, tone, specificity, usefulness, and quality of explanation. "
    "Do not decide policy status, factual support, grounding, safety, or whether an "
    "external action was allowed; the deterministic Harness owns those invariants. "
    "Use unknown when the response is insufficient to assess. Return a concise critique."
)
_RUN_ID_PATTERN = re.compile(r"^run_[A-Za-z0-9_-]+$")


OFFLINE_SOFT_CHECK_MODE = "offline_soft_check"


def candidate_artifact_path(run_id: str) -> Path:
    _validate_run_id(run_id)
    return persistent_artifact_path("lab_05", "runs", run_id, "candidate.json")


def judge_artifact_path(run_id: str) -> Path:
    _validate_run_id(run_id)
    return persistent_artifact_path("lab_05", "runs", run_id, "judge_summary.json")


def load_candidate_run(run_id: str) -> dict[str, Any]:
    path = candidate_artifact_path(run_id)
    if not path.is_file():
        raise FileNotFoundError(
            f"No current Lab 5 candidate run was found for {run_id}. "
            "Run Lab 5 Chat first and pass its run ID explicitly."
        )
    candidate = json.loads(path.read_text(encoding="utf-8"))
    if candidate.get("run_id") != run_id:
        raise ValueError(f"Candidate artifact lineage does not match {run_id}.")
    for field in ("request", "request_context", "candidate_response", "evidence_notes"):
        if field not in candidate:
            raise ValueError(f"Candidate artifact is missing {field}.")
    return candidate


def render_judge_prompt(candidate: dict[str, Any]) -> str:
    evidence = json.dumps(candidate["evidence_notes"], ensure_ascii=False, indent=2)
    request_context = candidate.get("request_context") or candidate["request"]
    return (
        f"{JUDGE_RUBRIC}\n\n"
        f"Bounded request context:\n{request_context}\n\n"
        f"Final candidate response:\n{candidate['candidate_response']}\n\n"
        f"Evidence notes and snippets:\n{evidence}\n"
    )


def offline_soft_check(candidate: dict[str, Any]) -> tuple[str, str]:
    """Validate judge input shape without adjudicating deterministic invariants."""
    response = candidate.get("candidate_response")
    if not isinstance(response, str) or not response.strip():
        return "unknown", "Offline soft-quality check could not inspect an empty candidate response."
    return (
        "unknown",
        "Candidate artifact is readable; soft quality requires the optional model judge. "
        "Deterministic policy, evidence, and safety invariants remain outside this judge.",
    )


def judge_candidate(
    candidate: dict[str, Any],
    *,
    session: LlmSession | None = None,
    judge_mode: str | None = None,
) -> dict[str, Any]:
    """Judge one current-run candidate without reading any other artifact."""
    if not candidate.get("run_id"):
        raise ValueError("Candidate run must include run_id.")
    mode = judge_mode or ("live" if session and session.live else OFFLINE_SOFT_CHECK_MODE)
    if mode == "replay":
        mode = OFFLINE_SOFT_CHECK_MODE
    if mode not in {"live", OFFLINE_SOFT_CHECK_MODE}:
        raise ValueError(f"Unsupported judge mode: {mode}")

    if mode == OFFLINE_SOFT_CHECK_MODE:
        verdict, critique = offline_soft_check(candidate)
    else:
        if session is None:
            raise ValueError("A live judge requires the selected provider session.")
        try:
            payload = session.complete_json(
                render_judge_prompt(candidate),
                JUDGE_RESPONSE_SCHEMA,
                offline_payload={
                    "verdict": "unknown",
                    "critique": "Live judge output was unavailable from the selected provider.",
                },
            )
        except (json.JSONDecodeError, KeyError, TypeError, IndexError, StopIteration) as exc:
            verdict = "unknown"
            critique = f"Live judge output could not be parsed: {exc}"
        except Exception as exc:
            verdict = "unknown"
            critique = f"Live judge request failed: {type(exc).__name__}: {exc}"
        else:
            if not isinstance(payload, dict):
                payload = {}
            verdict = payload.get("verdict")
            critique = payload.get("critique")
            if verdict not in JUDGE_VERDICTS or not isinstance(critique, str):
                verdict = "unknown"
                critique = "Judge output was not in the expected verdict-and-critique shape."
    return {
        "candidate_run_id": candidate["run_id"],
        "candidate_mode": candidate.get("candidate_mode", "unknown"),
        "judge_mode": mode,
        "verdict": verdict,
        "critique": critique,
    }


def run_judge(
    run_id: str,
    *,
    session: LlmSession | None = None,
    offline_check: bool = False,
    output_path: Path | None = None,
) -> dict[str, Any]:
    candidate = load_candidate_run(run_id)
    if offline_check:
        summary = judge_candidate(candidate, judge_mode=OFFLINE_SOFT_CHECK_MODE)
    else:
        summary = judge_candidate(candidate, session=session or LlmSession())
    destination = output_path or judge_artifact_path(run_id)
    try:
        summary["artifact"] = f"artifacts/{relative_artifact_path(destination)}"
    except ValueError:
        summary["artifact"] = destination.as_posix()
    write_json(destination, summary)
    return summary


def _validate_run_id(run_id: str) -> None:
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id must be an explicit Lab 5 run identifier such as run_abc123.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the optional Lab 5 LLM-as-judge demo.")
    parser.add_argument(
        "--run-id",
        required=True,
        help="Current Lab 5 Chat run ID; stale latest artifacts are never used.",
    )
    parser.add_argument(
        "--offline-check",
        action="store_true",
        help="Validate the candidate artifact without judging soft quality or deterministic invariants.",
    )
    args = parser.parse_args()
    summary = run_judge(args.run_id, offline_check=args.offline_check)
    print(f"candidate_run_id={summary['candidate_run_id']}")
    print(f"candidate_mode={summary['candidate_mode']}")
    print(f"judge_mode={summary['judge_mode']}")
    print(f"verdict={summary['verdict']}")
    print(f"critique={summary['critique']}")
    print(f"artifact={summary['artifact']}")


if __name__ == "__main__":
    main()
