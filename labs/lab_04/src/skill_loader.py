"""Course-provided Skill loading.

A Skill is a file of reusable behavioural rules that the runner loads and puts
in front of the model. Two properties make it worth having as a file rather than
a string literal inside a prompt:

- the same rules can be reused by every run and every caller, and
- what the model was told is recorded, so a bad output can be traced back to the
  rules that were (or were not) in effect.

Loading is not enforcement. These rules change what the model *tends* to
produce; `build_evidence_notes()` is what actually decides whether a claim is
supported. Keep both, and do not let a well-written Skill talk you out of the
verifier.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from labs.shared.config import ROOT_DIR
from labs.lab_04.src.context_budget import estimate_tokens


SKILL_PATH = ROOT_DIR / "labs" / "lab_04" / "skills" / "job-prep" / "SKILL.md"
RELATIVE_SKILL_PATH = "labs/lab_04/skills/job-prep/SKILL.md"


class LoadedSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loaded: bool
    path: str | None = None
    name: str | None = None
    description: str | None = None
    rules: list[str] = []
    rule_count: int = 0
    estimated_tokens: int = 0


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a leading `---` block into key/value pairs plus the body."""
    if not text.startswith("---"):
        return {}, text

    _, _, remainder = text.partition("\n")
    block, separator, body = remainder.partition("\n---")
    if not separator:
        return {}, text

    fields: dict[str, str] = {}
    for line in block.splitlines():
        key, delimiter, value = line.partition(":")
        if delimiter:
            fields[key.strip()] = value.strip()
    return fields, body.lstrip("\n")


def load_skill(use_skill: bool = True, path: Path | None = None) -> LoadedSkill:
    if not use_skill:
        return LoadedSkill(loaded=False)

    text = (path or SKILL_PATH).read_text(encoding="utf-8")
    if "TODO(lab_04)" in text:
        raise RuntimeError("Complete the Lab 4 Job Prep Skill rules before running this stage.")

    fields, body = parse_frontmatter(text)
    rules = [line.strip()[2:].strip() for line in body.splitlines() if line.strip().startswith("- ")]
    prompt_block = render_skill_prompt(fields.get("name"), rules)
    return LoadedSkill(
        loaded=True,
        path=RELATIVE_SKILL_PATH,
        name=fields.get("name"),
        description=fields.get("description"),
        rules=rules,
        rule_count=len(rules),
        estimated_tokens=estimate_tokens(prompt_block),
    )


def render_skill_prompt(name: str | None, rules: list[str]) -> str:
    if not rules:
        return ""

    rendered_rules = "\n".join(f"- {rule}" for rule in rules)
    return f"Follow these {name or 'job-prep'} rules:\n{rendered_rules}\n"


def skill_prompt_block(skill: LoadedSkill) -> str:
    """Render the loaded rules for the model prompt.

    This block is protected content: it is reserved before sources compete for
    budget in `select_context()`, so a tight budget drops evidence rather than
    the rules that govern how evidence must be used.
    """
    if not skill.loaded or not skill.rules:
        return ""

    return render_skill_prompt(skill.name, skill.rules)
