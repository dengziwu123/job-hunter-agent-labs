from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


MaterialKind = Literal["candidate_profile", "job_description", "web_source"]


class ApiKeyRequest(BaseModel):
    api_key: str


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=50_000)


class HarnessEvent(BaseModel):
    sequence: int
    type: str
    status: Literal["started", "completed", "failed", "blocked"]
    component: str
    operation: str
    summary: str
    duration_ms: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ArtifactLink(BaseModel):
    label: str
    path: str


class ErrorInfo(BaseModel):
    kind: str
    message: str
    file: str | None = None
    line: int | None = None


class ChatRequest(BaseModel):
    stage: str
    backend: Literal["thin_harness", "openclaw"] = "thin_harness"
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,100}$")
    workspace_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,100}$")
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)


class ChatResponse(BaseModel):
    status: Literal["ok", "error"]
    stage: str
    run_id: str
    assistant_message: str = ""
    events: list[HarnessEvent] = Field(default_factory=list)
    state_summary: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactLink] = Field(default_factory=list)
    error: ErrorInfo | None = None


class EvalResponse(BaseModel):
    status: Literal["ok", "error"]
    stage: str
    run_id: str
    events: list[HarnessEvent] = Field(default_factory=list)
    artifacts: list[ArtifactLink] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    error: ErrorInfo | None = None


class EvalRequest(BaseModel):
    stage: str
    workspace_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,100}$")


class TextMaterialRequest(BaseModel):
    workspace_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,100}$")
    kind: MaterialKind
    display_name: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=250_000)


class UrlMaterialRequest(BaseModel):
    workspace_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,100}$")
    url: str = Field(min_length=8, max_length=2_048)


class JobMaterialPublic(BaseModel):
    material_id: str
    kind: MaterialKind
    display_name: str
    status: Literal["pending", "ready", "error"] = "ready"
    characters: int
    preview: str
    source: Literal["fixture", "paste", "upload", "web"]
    source_url: str | None = None


class CapabilityExample(BaseModel):
    id: str
    prompt: str
    observe: list[str]
    compare_note: str


class StagePublic(BaseModel):
    id: str
    title: str
    phase: str
    available: bool
    capabilities: list[str]
    now_you_can: list[str]
    examples: list[CapabilityExample]
    still_cannot: str
    previous_stage: str | None = None
