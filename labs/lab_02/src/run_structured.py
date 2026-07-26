from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Protocol

from labs.shared.artifacts import artifact_path, read_json, write_json
from labs.shared.config import Settings, load_settings
from labs.lab_01.src.model_client import ModelClient
from labs.lab_02.src.schemas import CandidateProfile, JobDescription, validate_fit_gap_report
from labs.lab_02.src.state_store import create_initial_state, mark_report_generated


class StructuredReportClient(Protocol):
    def generate_report_json(
        self,
        profile: CandidateProfile,
        job_description: JobDescription,
    ) -> dict[str, Any]:
        ...


class GeminiStructuredReportClient:
    def __init__(self, settings: Settings, model_client: ModelClient | None = None):
        self.settings = settings
        self.model_client = model_client or ModelClient(settings)

    def generate_report_json(
        self,
        profile: CandidateProfile,
        job_description: JobDescription,
    ) -> dict[str, Any]:
        required_fields = [
            "fit_summary",
            "strengths",
            "gaps",
            "risks",
            "missing_info",
            "recommended_next_steps",
        ]
        messages = [
            {
                "role": "system",
                "content": (
                    "Analyze candidate fit for the job description. Return only one JSON object "
                    "with exactly these required fields: "
                    f"{', '.join(required_fields)}. "
                    "fit_summary must be a string; every other field must be a list of strings. "
                    "Do not include markdown fences or extra fields."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Candidate profile:\n"
                    f"{json.dumps(profile.model_dump(), ensure_ascii=False)}\n\n"
                    "Job description:\n"
                    f"{json.dumps(job_description.model_dump(), ensure_ascii=False)}"
                ),
            },
        ]
        assistant_text = self.model_client.complete(messages)
        data = json.loads(assistant_text)
        if not isinstance(data, dict):
            raise ValueError("Model response must be a JSON object.")
        return data


def load_profile(path: Path) -> CandidateProfile:
    return CandidateProfile.model_validate(read_json(path))


def load_job_description(path: Path) -> JobDescription:
    return JobDescription.model_validate(read_json(path))


def run(
    profile_path: Path,
    jd_path: Path,
    client: StructuredReportClient | None = None,
) -> dict[str, Any]:
    profile = load_profile(profile_path)
    job_description = load_job_description(jd_path)
    return run_from_objects(profile, job_description, client=client)


def run_from_objects(
    profile: CandidateProfile,
    job_description: JobDescription,
    client: StructuredReportClient | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    state = create_initial_state(profile, job_description)
    report_client = client or GeminiStructuredReportClient(load_settings())

    report = validate_fit_gap_report(report_client.generate_report_json(profile, job_description))
    output_path = output_path or artifact_path("lab_02", "job_prep_report.json")
    state = mark_report_generated(state, output_path)

    payload = {
        "profile": profile.model_dump(),
        "job_description": job_description.model_dump(),
        "report": report.model_dump(),
        "state": state.model_dump(),
        "validation": {"status": "valid", "schema": "FitGapReport"},
    }
    write_json(output_path, payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--track", default="job_hunting")
    parser.add_argument("--profile", type=Path, default=Path("labs/lab_02/data/profile.json"))
    parser.add_argument("--jd", type=Path, default=Path("labs/lab_02/data/job_description.json"))
    args = parser.parse_args()

    if args.track != "job_hunting":
        raise SystemExit("Only the job_hunting track is included in the starter repo.")

    payload = run(args.profile, args.jd)
    print("track=job_hunting")
    print(f"status={payload['state']['status']}")
    print("artifact=artifacts/lab_02/job_prep_report.json")


if __name__ == "__main__":
    main()
