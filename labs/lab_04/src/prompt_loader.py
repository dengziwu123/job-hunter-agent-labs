"""Load and render the student-authored task prompt."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from labs.lab_04.src.context_budget import estimate_tokens
from labs.shared.config import ROOT_DIR


PROMPT_PATH = ROOT_DIR / "labs" / "lab_04" / "prompts" / "grounded-job-research.md"
RELATIVE_PROMPT_PATH = "labs/lab_04/prompts/grounded-job-research.md"
REQUIRED_PLACEHOLDERS = (
    "{{skill_rules}}",
    "{{user_request}}",
    "{{evidence_sources}}",
    "{{available_tools}}",
    "{{job_openings}}",
)

# Prose that ships in the starter. Students may keep it, but it is not their
# instruction, so it does not satisfy the authored-content check below.
SCAFFOLD_PROSE = (
    "Keep every `{{placeholder}}` below. The course runtime fills them with the "
    "current request, evidence, MCP declarations, and tool results.",
    "The complete selected records appear in the evidence section above. These "
    "references identify which MCP results survived the evidence budget.",
)


class LoadedTaskPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    template: str
    placeholders: list[str]
    estimated_tokens: int


def authored_instruction(text: str) -> str:
    """Return student-authored content after removing scaffold and placeholders.

    Headings, placeholder lines, and the prose that shipped in the starter are
    all removed first. This only detects an empty prompt; the Diff and a live run
    show whether the remaining instruction is any good.
    """
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for placeholder in REQUIRED_PLACEHOLDERS:
            stripped = stripped.replace(placeholder, " ")
        if stripped.strip():
            lines.append(stripped)

    remainder = " ".join(" ".join(lines).split())
    for prose in SCAFFOLD_PROSE:
        remainder = remainder.replace(" ".join(prose.split()), " ")
    return remainder.strip()


def load_task_prompt(path: Path | None = None) -> LoadedTaskPrompt:
    text = (path or PROMPT_PATH).read_text(encoding="utf-8").strip()
    if "TODO(lab_04)" in text:
        raise RuntimeError("Complete the Lab 4 grounded job-research prompt before running this stage.")
    missing = [placeholder for placeholder in REQUIRED_PLACEHOLDERS if placeholder not in text]
    if missing:
        raise RuntimeError(f"The Lab 4 task prompt is missing placeholder(s): {', '.join(missing)}")
    if not authored_instruction(text):
        raise RuntimeError(
            "The Lab 4 task prompt has no instruction of your own. Placeholders alone "
            "tell the model nothing: say when to research current openings, how to "
            "compare a job requirement against candidate evidence, what to do when "
            "support is missing, and what claim output to return."
        )
    return LoadedTaskPrompt(
        path=RELATIVE_PROMPT_PATH,
        template=text,
        placeholders=list(REQUIRED_PLACEHOLDERS),
        estimated_tokens=estimate_tokens(text),
    )


def render_task_prompt(
    prompt: LoadedTaskPrompt,
    *,
    skill_rules: str,
    user_request: str,
    evidence_sources: str,
    available_tools: list[dict] | None = None,
    job_openings: list[dict] | None = None,
) -> str:
    values = {
        "{{skill_rules}}": skill_rules,
        "{{user_request}}": user_request.strip(),
        "{{evidence_sources}}": evidence_sources.strip(),
        "{{available_tools}}": json.dumps(available_tools or [], indent=2, ensure_ascii=False),
        "{{job_openings}}": json.dumps(job_openings or [], indent=2, ensure_ascii=False),
    }
    rendered = prompt.template
    for placeholder, value in values.items():
        rendered = rendered.replace(placeholder, value)
    return rendered
