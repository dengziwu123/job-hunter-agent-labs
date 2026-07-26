from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import labs.shared.artifacts as artifacts
from labs.shared.web.contracts import ArtifactLink, ChatRequest, ChatResponse, EvalResponse
from labs.shared.web.errors import StageExecutionError
from labs.shared.web.materials import MaterialStore
from labs.shared.web.registry import StageRegistration


@dataclass(frozen=True)
class ManagedStateCheckpoint:
    state_path: Path
    state_contents: bytes | None
    revision_contents: dict[Path, bytes]

    @classmethod
    def capture(cls, stage_id: str, workspace_id: str) -> ManagedStateCheckpoint:
        directory = artifacts.task_state_directory(stage_id, workspace_id)
        state_path = artifacts.task_state_path(stage_id, workspace_id)
        revision_directory = directory / "revisions"
        return cls(
            state_path=state_path,
            state_contents=state_path.read_bytes() if state_path.is_file() else None,
            revision_contents={
                path: path.read_bytes()
                for path in revision_directory.glob("revision_*.json")
            },
        )

    def restore(self) -> None:
        if self.state_contents is None:
            self.state_path.unlink(missing_ok=True)
        else:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_bytes(self.state_contents)
        revision_directory = self.state_path.parent / "revisions"
        for path, contents in self.revision_contents.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
        if revision_directory.is_dir():
            for path in revision_directory.glob("revision_*.json"):
                if path not in self.revision_contents:
                    path.unlink()

    def without_rolled_back_state_artifacts(
        self,
        links: list[ArtifactLink],
    ) -> list[ArtifactLink]:
        state_directory = self.state_path.parent.resolve()
        artifact_root = (artifacts.ROOT_DIR / "artifacts").resolve()
        return [
            link
            for link in links
            if not (artifact_root / link.path).resolve().is_relative_to(state_directory)
        ]


@dataclass(frozen=True)
class StageExecutor:
    """Execute one complete stage from the request's raw workspace inputs."""

    load_adapter: Callable[[str], type]

    def chat(
        self,
        *,
        registration: StageRegistration,
        materials: MaterialStore,
        request: ChatRequest,
    ) -> ChatResponse:
        if request.stage != registration.public.id:
            raise ValueError("Chat request stage does not match the selected stage.")
        adapter = self.load_adapter(registration.adapter)(materials)
        checkpoint = (
            ManagedStateCheckpoint.capture(request.stage, request.workspace_id)
            if "state_management" in registration.effective_capabilities
            else None
        )
        try:
            response = adapter.chat(request)
            if response.stage != request.stage:
                raise ValueError("Stage adapter returned a response for a different stage.")
            if checkpoint is not None and response.status != "ok":
                checkpoint.restore()
                response = response.model_copy(
                    update={
                        "artifacts": checkpoint.without_rolled_back_state_artifacts(
                            response.artifacts,
                        ),
                    },
                )
            return response
        except StageExecutionError as exc:
            if checkpoint is not None:
                checkpoint.restore()
                exc.artifacts = checkpoint.without_rolled_back_state_artifacts(exc.artifacts)
            raise
        except Exception:
            if checkpoint is not None:
                checkpoint.restore()
            raise

    def eval(
        self,
        *,
        registration: StageRegistration,
        materials: MaterialStore,
        workspace_id: str,
    ) -> EvalResponse:
        adapter = self.load_adapter(registration.adapter)(materials)
        response = adapter.run_eval(workspace_id)
        if response.stage != registration.public.id:
            raise ValueError("Stage adapter returned an eval for a different stage.")
        return response
