from __future__ import annotations

import json
from pathlib import Path

from labs.shared.artifacts import artifact_path, relative_artifact_path
from labs.shared.config import ROOT_DIR
from labs.shared.web.contracts import ArtifactLink, HarnessEvent


def write_run_trace(stage: str, run_id: str, events: list[HarnessEvent]) -> ArtifactLink:
    path = artifact_path(stage, "runs", run_id, "trace.jsonl")
    content = "".join(
        json.dumps(
            {"run_id": run_id, "stage": stage, **event.model_dump()},
            ensure_ascii=False,
        )
        + "\n"
        for event in events
    )
    path.write_text(content, encoding="utf-8")
    return ArtifactLink(
        label=f"{stage.replace('_', ' ').title()} call trace",
        path=relative_artifact_path(path),
    )


def record_run_trace(stage: str, run_id: str, events: list[HarnessEvent]) -> ArtifactLink:
    events.append(
        HarnessEvent(
            sequence=len(events) + 1,
            type="trace",
            status="completed",
            component="labs.shared.web.tracing",
            operation="record_run_trace",
            summary="Recorded the complete current-stage event stream",
            details={"stage": stage},
        )
    )
    return write_run_trace(stage, run_id, events)


def safe_artifact_path(relative_path: str) -> Path:
    root = (ROOT_DIR / "artifacts").resolve()
    requested = (root / relative_path).resolve()
    if requested != root and root not in requested.parents:
        raise ValueError("Artifact path must stay inside artifacts/.")
    if requested.suffix not in {".json", ".jsonl", ".md"}:
        raise ValueError("Only JSON, JSONL, and Markdown artifacts can be viewed.")
    return requested
