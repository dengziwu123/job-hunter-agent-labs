from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from labs.lab_02.src.schemas import CandidateProfile, JobDescription


class JobPrepState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: CandidateProfile
    job_description: JobDescription
    status: str = "started"
    latest_report_artifact: str | None = None
    validation_status: str = "not_started"


def create_initial_state(profile: CandidateProfile, job_description: JobDescription) -> JobPrepState:
    return JobPrepState(profile=profile, job_description=job_description)


def mark_report_generated(state: JobPrepState, artifact_path: Path) -> JobPrepState:
    path_parts = artifact_path.parts
    try:
        artifacts_index = path_parts.index("artifacts")
    except ValueError:
        portable_path = artifact_path.as_posix()
    else:
        portable_path = Path(*path_parts[artifacts_index:]).as_posix()

    return state.model_copy(
        update={
            "status": "report_generated",
            "latest_report_artifact": portable_path,
            "validation_status": "valid",
        }
    )
