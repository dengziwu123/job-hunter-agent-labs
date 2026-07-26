from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


MaterialKind = Literal["candidate_profile", "job_description", "web_source"]


class ApiKeyRequest(BaseModel):
    api_key: str
    provider: Literal["gemini", "openai", "anthropic"] | None = None
    model: str = ""


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
    request_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,100}$")
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


class TraceParticipant(BaseModel):
    participant_id: str
    kind: Literal["workflow", "coordinator", "agent", "runtime"]
    label: str
    role: str | None = None


class TraceSpan(BaseModel):
    span_id: str
    parent_span_id: str | None = None
    participant_id: str
    semantic_key: str
    kind: str
    component: str
    operation: str
    summary: str
    status: Literal["started", "completed", "failed", "blocked"]
    start_offset_ms: int
    duration_ms: int | None = None
    input_contract: str | None = None
    output_contract: str | None = None
    context_sources: list[str] = Field(default_factory=list)
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    budget_delta: dict[str, int] = Field(default_factory=dict)


class TraceLink(BaseModel):
    link_id: str
    kind: Literal["calls", "delegates", "handoff"]
    source_span_id: str
    target_span_id: str
    contract_fields: list[str] = Field(default_factory=list)
    summary: str


class RunTrace(BaseModel):
    trace_id: str
    participants: list[TraceParticipant] = Field(default_factory=list)
    spans: list[TraceSpan] = Field(default_factory=list)
    links: list[TraceLink] = Field(default_factory=list)


class ComparisonRequest(BaseModel):
    current_stage: str
    backend: Literal["thin_harness", "openclaw"] = "thin_harness"
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,100}$")
    workspace_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,100}$")
    request_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,100}$")
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)


class ComparisonRun(BaseModel):
    stage: str
    run_id: str
    input_snapshot_id: str
    status: Literal["ok", "error"]
    assistant_message: str = ""
    events: list[HarnessEvent] = Field(default_factory=list)
    trace: RunTrace
    state_summary: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactLink] = Field(default_factory=list)
    error: ErrorInfo | None = None
    artifact_root: str = ""


class ComparisonResponse(BaseModel):
    comparison_id: str
    input_snapshot: dict[str, Any]
    before: ComparisonRun
    after: ComparisonRun
    delta: dict[str, Any] = Field(default_factory=dict)
    compare_note: str = ""


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
