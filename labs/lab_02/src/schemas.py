from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CandidateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    headline: str
    skills: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class JobDescription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    company: str
    requirements: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)


class FitGapReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fit_summary: str
    strengths: list[str]
    gaps: list[str]
    risks: list[str]
    missing_info: list[str]
    recommended_next_steps: list[str]


def validate_fit_gap_report(data: dict) -> FitGapReport:
    return FitGapReport.model_validate(data)
