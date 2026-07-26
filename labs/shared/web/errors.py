from __future__ import annotations

from labs.shared.web.contracts import ArtifactLink, HarnessEvent


class StageExecutionError(Exception):
    def __init__(
        self,
        original: Exception,
        events: list[HarnessEvent],
        artifacts: list[ArtifactLink],
        run_id: str,
    ) -> None:
        super().__init__(str(original))
        self.original = original
        self.events = events
        self.artifacts = artifacts
        self.run_id = run_id
