from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from labs.shared.web.contracts import StagePublic


LABS_DIR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class StageRegistration:
    public: StagePublic
    adapter: str
    effective_capabilities: frozenset[str]


def load_registry() -> dict[str, StageRegistration]:
    registry: dict[str, StageRegistration] = {}
    for manifest_path in sorted(LABS_DIR.glob("lab_*/stage.json")):
        raw_stage = json.loads(manifest_path.read_text(encoding="utf-8"))
        adapter = raw_stage.pop("adapter")
        public = StagePublic.model_validate(raw_stage)
        if public.id != manifest_path.parent.name:
            raise ValueError(f"Stage manifest id must match its Lab directory: {manifest_path}")
        if public.previous_stage and public.previous_stage not in registry:
            # A progressive package cannot run this stage or any descendant
            # without the code and inherited capabilities from its predecessor.
            continue
        inherited = (
            registry[public.previous_stage].effective_capabilities
            if public.previous_stage
            else frozenset()
        )
        registry[public.id] = StageRegistration(
            public=public,
            adapter=adapter,
            effective_capabilities=inherited | frozenset(public.capabilities),
        )
    return registry
