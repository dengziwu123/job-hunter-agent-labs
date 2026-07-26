from __future__ import annotations

import json


KNOWN_SKILLS = [
    "Python",
    "SQL",
    "API integration",
    "workflow automation",
    "LLM evaluation",
    "product analytics",
]


def material_to_profile(record: dict):
    from labs.lab_02.src.schemas import CandidateProfile

    data = parse_json_object(record["text"])
    if data is not None:
        return CandidateProfile.model_validate(data)

    text = record["text"]
    lowered = text.lower()
    skills = [skill for skill in KNOWN_SKILLS if skill.lower() in lowered]
    return CandidateProfile(
        id=record["material_id"],
        headline=first_line(text, "Uploaded candidate profile"),
        skills=skills,
        projects=[text[:8_000]],
        constraints=[],
    )


def material_to_job_description(record: dict):
    from labs.lab_02.src.schemas import JobDescription

    data = parse_json_object(record["text"])
    if data is not None:
        return JobDescription.model_validate(data)

    text = record["text"]
    requirements = [line.strip("-• ") for line in text.splitlines() if line.strip()]
    return JobDescription(
        id=record["material_id"],
        title=first_line(text, "Uploaded job description"),
        company="Unknown company",
        requirements=requirements[:20] or [text[:8_000]],
        nice_to_have=[],
    )


def active_material(records: list[dict], kind: str) -> dict:
    matches = [record for record in records if record["kind"] == kind]
    if not matches:
        label = kind.replace("_", " ")
        raise ValueError(f"Add a {label} in Job Materials before running this Lab.")
    return matches[-1]


def parse_json_object(text: str) -> dict | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def first_line(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:160]
    return fallback
