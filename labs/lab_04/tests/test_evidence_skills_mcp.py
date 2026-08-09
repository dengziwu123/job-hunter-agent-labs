from __future__ import annotations

import json
from pathlib import Path

import pytest

import labs.lab_04.src.retrieval as retrieval
import labs.lab_04.web_adapter as web_adapter
from labs.lab_01.src.model_client import ModelClient
from labs.shared.config import Settings
from labs.lab_04.web_adapter import Lab04Adapter, build_evidence_capability, format_evidence_response
from labs.shared.web.contracts import ChatRequest
from labs.shared.web.materials import MaterialStore
from labs.lab_04.src.evidence import ClaimInput, EvidenceNote, build_evidence_notes
from labs.lab_04.src.mcp_client_adapter import ping_mcp_example
from labs.lab_04.src.retrieval import SourceRecord, retrieve_sources
from labs.lab_04.src.skill_loader import LoadedSkill
from labs.shared.web.comparisons import normalize_trace


def test_retrieval_returns_source_records() -> None:
    sources = retrieve_sources("Python LLM API", limit=2)

    assert len(sources) == 2
    assert all(isinstance(source, SourceRecord) for source in sources)
    assert sources[0].source_id
    assert sources[0].snippet


def test_retrieval_reuses_lab_3_search_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int, list[dict] | None]] = []

    def fake_search(query: str, limit: int, source_items: list[dict] | None = None):
        calls.append((query, limit, source_items))
        return [
            SourceRecord(
                source_id="source-from-lab-3",
                title="Lab 3 source",
                path="job-material://source-from-lab-3",
                snippet="Python API integration experience.",
            )
        ]

    monkeypatch.setattr(retrieval, "search_sources", fake_search)

    sources = retrieval.retrieve_sources("Python API", limit=1, source_items=[{"source_id": "input"}])

    assert calls == [("Python API", 1, [{"source_id": "input"}])]
    assert sources[0].source_id == "source-from-lab-3"


def test_evidence_marks_supported_and_unsupported_claims() -> None:
    sources = [
        SourceRecord(
            source_id="source-profile-001",
            title="Profile",
            path="profile.json",
            snippet="The candidate has Python API integration experience.",
        )
    ]
    claims = [
        ClaimInput(claim="The candidate has Python API integration experience.", source_id="source-profile-001"),
        ClaimInput(claim="The candidate led a production multi-agent migration.", source_id=None),
        ClaimInput(claim="The candidate managed Kubernetes clusters.", source_id="source-profile-001"),
        # A live model paraphrases rather than quoting. This claim says the same
        # thing as the snippet in different words and must still be supported —
        # requiring an exact match would mark real evidence unsupported.
        ClaimInput(
            claim="The candidate has professional experience with Python API integration.",
            source_id="source-profile-001",
        ),
        ClaimInput(
            claim="The candidate has no Python API integration experience.",
            source_id="source-profile-001",
        ),
    ]

    notes = build_evidence_notes(claims, sources)

    assert all(isinstance(note, EvidenceNote) for note in notes)
    assert notes[0].status == "supported"
    assert notes[0].source_id == "source-profile-001"
    assert notes[0].supporting_snippet
    assert notes[1].status == "unsupported"
    assert notes[1].source_id is None
    assert notes[2].status == "unsupported"
    assert notes[2].source_id == "source-profile-001"
    assert notes[2].supporting_snippet is None
    assert notes[3].status == "supported"
    assert notes[3].source_id == "source-profile-001"
    assert notes[3].supporting_snippet
    assert notes[4].status == "unsupported"
    assert notes[4].source_id == "source-profile-001"
    assert notes[4].supporting_snippet is None


def test_evidence_response_only_displays_llm_claim_text() -> None:
    response = format_evidence_response(
        [
            {
                "claim": "The candidate has Python experience.",
                "status": "supported",
                "source_id": "source-profile-001",
            },
            {
                "claim": "The candidate led a production migration.",
                "status": "unsupported",
                "source_id": "source-profile-001",
            },
        ]
    )

    assert response == (
        "The candidate has Python experience.\n"
        "The candidate led a production migration."
    )
    assert "supported" not in response.lower()
    assert "source-profile-001" not in response


def test_evidence_rejects_conflicting_numeric_claims() -> None:
    source = SourceRecord(
        source_id="source-years",
        title="Profile",
        path="profile.json",
        snippet="The candidate has 2 years of Python experience.",
    )

    note = build_evidence_notes(
        [
            ClaimInput(
                claim="The candidate has 5 years of Python experience.",
                source_id=source.source_id,
            )
        ],
        [source],
    )[0]

    assert note.status == "unsupported"
    assert note.source_id == source.source_id
    assert note.supporting_snippet is None


def test_evidence_rejects_conflicting_skill_entities() -> None:
    source = SourceRecord(
        source_id="source-skill",
        title="Profile",
        path="profile.json",
        snippet="The candidate knows Python.",
    )

    note = build_evidence_notes(
        [ClaimInput(claim="The candidate knows Java.", source_id=source.source_id)],
        [source],
    )[0]

    assert note.status == "unsupported"
    assert note.source_id == source.source_id
    assert note.supporting_snippet is None


def test_evidence_preserves_punctuation_in_lowercase_skill_tokens() -> None:
    source = SourceRecord(
        source_id="source-punctuated-skill",
        title="Profile",
        path="profile.json",
        snippet="the candidate knows c#.",
    )

    note = build_evidence_notes(
        [ClaimInput(claim="the candidate knows c++.", source_id=source.source_id)],
        [source],
    )[0]

    assert note.status == "unsupported"
    assert note.source_id == source.source_id
    assert note.supporting_snippet is None


@pytest.mark.parametrize(
    "snippet",
    [
        "The role requires production Kubernetes experience.",
        "Production Kubernetes experience is required for this role.",
    ],
)
def test_evidence_does_not_treat_role_requirements_as_candidate_experience(
    snippet: str,
) -> None:
    source = SourceRecord(
        source_id="source-job-requirement",
        title="Job description",
        path="job-description.json",
        snippet=snippet,
    )

    note = build_evidence_notes(
        [
            ClaimInput(
                claim="The candidate has production Kubernetes experience.",
                source_id=source.source_id,
            )
        ],
        [source],
    )[0]

    assert note.status == "unsupported"
    assert note.supporting_snippet is None


@pytest.mark.parametrize(
    "snippet",
    [
        "The candidate doesn't have Python experience.",
        "The candidate cannot use Python.",
    ],
)
def test_evidence_recognizes_compound_negation_forms(snippet: str) -> None:
    source = SourceRecord(
        source_id="source-negative-skill",
        title="Profile",
        path="profile.json",
        snippet=snippet,
    )

    note = build_evidence_notes(
        [
            ClaimInput(
                claim="The candidate has Python experience.",
                source_id=source.source_id,
            )
        ],
        [source],
    )[0]

    assert note.status == "unsupported"
    assert note.supporting_snippet is None


def test_evidence_rejects_conflicting_lowercase_attributes() -> None:
    source = SourceRecord(
        source_id="source-domain",
        title="Profile",
        path="profile.json",
        snippet="The candidate has frontend experience.",
    )

    note = build_evidence_notes(
        [
            ClaimInput(
                claim="The candidate has backend experience.",
                source_id=source.source_id,
            )
        ],
        [source],
    )[0]

    assert note.status == "unsupported"
    assert note.source_id == source.source_id
    assert note.supporting_snippet is None


def test_evidence_rejects_conflicting_modifiers_despite_fuzzy_overlap() -> None:
    source = SourceRecord(
        source_id="source-api-audience",
        title="Profile",
        path="profile.json",
        snippet="The candidate builds internal APIs.",
    )

    note = build_evidence_notes(
        [
            ClaimInput(
                claim="The candidate builds public APIs.",
                source_id=source.source_id,
            )
        ],
        [source],
    )[0]

    assert note.status == "unsupported"
    assert note.source_id == source.source_id
    assert note.supporting_snippet is None


def test_evidence_rejects_conflicting_predicates_despite_shared_objects() -> None:
    source = SourceRecord(
        source_id="source-offer-decision",
        title="Profile",
        path="profile.json",
        snippet="The candidate rejected the Stripe offer.",
    )

    note = build_evidence_notes(
        [
            ClaimInput(
                claim="The candidate accepted the Stripe offer.",
                source_id=source.source_id,
            )
        ],
        [source],
    )[0]

    assert note.status == "unsupported"
    assert note.source_id == source.source_id
    assert note.supporting_snippet is None


def test_evidence_matches_skill_entities_without_requiring_source_casing() -> None:
    source = SourceRecord(
        source_id="source-lowercase-skill",
        title="Profile",
        path="profile.json",
        snippet="the candidate uses python.",
    )

    note = build_evidence_notes(
        [
            ClaimInput(
                claim="The candidate uses Python.",
                source_id=source.source_id,
            )
        ],
        [source],
    )[0]

    assert note.status == "supported"
    assert note.source_id == source.source_id
    assert note.supporting_snippet == source.snippet


def test_evidence_ignores_capitalization_caused_only_by_sentence_position() -> None:
    source = SourceRecord(
        source_id="source-paraphrase",
        title="Profile",
        path="profile.json",
        snippet="builds internal developer tools.",
    )

    note = build_evidence_notes(
        [
            ClaimInput(
                claim="Develops internal tools.",
                source_id=source.source_id,
            )
        ],
        [source],
    )[0]

    assert note.status == "supported"
    assert note.supporting_snippet == source.snippet


def test_skill_file_contains_required_rules() -> None:
    skill_path = Path(__file__).resolve().parents[1] / "skills" / "job-prep" / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8").lower()

    assert "todo(lab_04)" not in text
    assert "- " in text
    assert "source_id" in text
    assert "unsupported" in text
    assert "draft-only" in text
    assert "human approval" in text


def test_skill_rules_reach_the_model_prompt() -> None:
    from labs.lab_04.src.context_budget import estimate_tokens
    from labs.lab_04.src.skill_loader import load_skill, skill_prompt_block

    skill = load_skill(use_skill=True)

    assert skill.loaded
    assert skill.name == "job-prep"
    assert skill.description
    assert skill.rule_count >= 3
    assert skill.estimated_tokens > 0

    block = skill_prompt_block(skill)
    assert all(rule in block for rule in skill.rules)
    assert skill.estimated_tokens == estimate_tokens(block)
    assert skill_prompt_block(load_skill(use_skill=False)) == ""


def test_task_prompt_loads_all_runtime_placeholders() -> None:
    from labs.lab_04.src.prompt_loader import REQUIRED_PLACEHOLDERS, load_task_prompt

    prompt = load_task_prompt()

    assert prompt.path.endswith("prompts/grounded-job-research.md")
    assert prompt.estimated_tokens > 0
    assert set(prompt.placeholders) == set(REQUIRED_PLACEHOLDERS)
    assert "TODO(lab_04)" not in prompt.template


def test_task_prompt_rejects_a_missing_placeholder(tmp_path: Path) -> None:
    from labs.lab_04.src.prompt_loader import load_task_prompt

    path = tmp_path / "prompt.md"
    path.write_text("{{skill_rules}} {{user_request}}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="missing placeholder"):
        load_task_prompt(path)


def test_task_prompt_rejects_placeholders_with_no_instruction(tmp_path: Path) -> None:
    """A prompt can satisfy every structural rule and still instruct nothing.

    The runtime would send this to the model and produce a trace that looks
    grounded, so the placeholders-only shell has to fail loudly instead.
    """
    from labs.lab_04.src.prompt_loader import load_task_prompt

    path = tmp_path / "prompt.md"
    path.write_text(
        "# Grounded Job Research Task\n\n"
        "## Reusable Skill rules\n\n{{skill_rules}}\n\n"
        "## User request\n\n{{user_request}}\n\n"
        "## Candidate and role evidence\n\n{{evidence_sources}}\n\n"
        "## Available MCP tools\n\n{{available_tools}}\n\n"
        "## Current-opening result references\n\n{{job_openings}}\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="no instruction of your own"):
        load_task_prompt(path)


def test_starter_scaffold_prose_does_not_count_as_instruction(tmp_path: Path) -> None:
    """Keeping the shipped prose must not be enough to clear the floor."""
    from labs.lab_04.src.prompt_loader import (
        SCAFFOLD_PROSE,
        authored_instruction,
        load_task_prompt,
    )

    assert authored_instruction("\n".join(SCAFFOLD_PROSE)) == ""

    # Rewrapping the shipped prose must not turn it into credit either.
    rewrapped = " ".join(" ".join(SCAFFOLD_PROSE).split())
    assert authored_instruction(rewrapped) == ""

    # A prompt built only from headings, placeholders, and shipped prose fails.
    path = tmp_path / "prompt.md"
    path.write_text(
        "# Grounded Job Research Task\n\n"
        f"{SCAFFOLD_PROSE[0]}\n\n"
        "## Reusable Skill rules\n\n{{skill_rules}}\n\n"
        "## User request\n\n{{user_request}}\n\n"
        "## Candidate and role evidence\n\n{{evidence_sources}}\n\n"
        "## Available MCP tools\n\n{{available_tools}}\n\n"
        f"## Current-opening result references\n\n{SCAFFOLD_PROSE[1]}\n\n{{{{job_openings}}}}\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="no instruction of your own"):
        load_task_prompt(path)


def test_sample_task_prompt_contains_authored_instruction() -> None:
    from labs.lab_04.src.prompt_loader import (
        authored_instruction,
        load_task_prompt,
    )

    template = load_task_prompt().template

    assert authored_instruction(template)


def test_task_prompt_accepts_student_instruction_without_space_delimited_words(
    tmp_path: Path,
) -> None:
    from labs.lab_04.src.prompt_loader import REQUIRED_PLACEHOLDERS, load_task_prompt

    path = tmp_path / "prompt.md"
    path.write_text(
        "判断是否需要查询当前职位；比较职位要求与候选人证据；缺少支持时留空来源。\n"
        + "\n".join(REQUIRED_PLACEHOLDERS),
        encoding="utf-8",
    )

    assert load_task_prompt(path).template.startswith("判断是否需要查询当前职位")


def test_course_mcp_path_uses_the_student_prompt_for_a_current_openings_decision() -> None:
    from labs.lab_04.src.job_research import JobResearchModel, research_job_board
    from labs.lab_04.src.prompt_loader import load_task_prompt

    result = research_job_board(
        task_prompt=load_task_prompt(),
        skill_rules="Never invent candidate experience.",
        user_request="Research current openings at Stripe on Greenhouse.",
        evidence_sources="- source_id=profile: Python API experience",
        model=JobResearchModel(Settings(model="offline-test", google_api_key="")),
    )

    assert result["status"] == "called"
    assert result["called_tool"] == "list_openings"
    assert result["arguments"] == {"company": "stripe", "ats": "greenhouse", "limit": 3}
    assert result["records"]
    assert [event["operation"] for event in result["protocol_operations"]] == [
        "initialize",
        "tools/list",
        "select_mcp_tool",
        "tools/call",
    ]
    model_input = result["model_io"]["actual_provider_input"]
    assert "Research current openings at Stripe" in model_input
    assert "Never invent candidate experience" in model_input
    assert "list_openings" in model_input


def test_offline_mcp_decision_reads_company_from_the_latest_request() -> None:
    from labs.lab_04.src.job_research import JobResearchModel, research_job_board
    from labs.lab_04.src.prompt_loader import load_task_prompt

    result = research_job_board(
        task_prompt=load_task_prompt(),
        skill_rules="Never invent candidate experience.",
        user_request=(
            "Continue the task. Regenerate the report from the raw profile.\n\n"
            "Earlier user requests:\nUSER: Research current roles at Acme.\n\n"
            "Latest user request:\nResearch current openings at Stripe on Greenhouse."
        ),
        evidence_sources="- source_id=profile: Python API experience",
        model=JobResearchModel(Settings(model="offline-test", google_api_key="")),
    )

    assert result["arguments"] == {"company": "stripe", "ats": "greenhouse", "limit": 3}


def test_offline_mcp_decision_preserves_prior_research_intent_for_a_follow_up() -> None:
    from labs.lab_04.src.job_research import JobResearchModel, research_job_board
    from labs.lab_04.src.prompt_loader import load_task_prompt

    result = research_job_board(
        task_prompt=load_task_prompt(),
        skill_rules="Never invent candidate experience.",
        user_request=(
            "Earlier user requests:\n"
            "USER: Research current openings at Stripe.\n\n"
            "Latest user request:\nWhat about Spotify?"
        ),
        evidence_sources="- source_id=profile: Python API experience",
        model=JobResearchModel(Settings(model="offline-test", google_api_key="")),
    )

    assert result["status"] == "called"
    assert result["arguments"] == {"company": "spotify", "ats": "lever", "limit": 3}
    assert result["records"]


def test_offline_mcp_decision_does_not_reuse_intent_for_an_unrelated_turn() -> None:
    from labs.lab_04.src.job_research import JobResearchModel, research_job_board
    from labs.lab_04.src.prompt_loader import load_task_prompt

    result = research_job_board(
        task_prompt=load_task_prompt(),
        skill_rules="Never invent candidate experience.",
        user_request=(
            "Earlier user requests:\n"
            "USER: Research current openings at Stripe.\n\n"
            "Latest user request:\nDraft an outreach note."
        ),
        evidence_sources="- source_id=profile: Python API experience",
        model=JobResearchModel(Settings(model="offline-test", google_api_key="")),
    )

    assert result["status"] == "skipped"
    assert result["arguments"] is None


@pytest.mark.parametrize(
    "user_request",
    [
        "Find jobs for me at Stripe.",
        "What roles can I apply for at Stripe?",
    ],
)
def test_offline_mcp_decision_uses_the_explicit_company_not_a_reference_word(
    user_request: str,
) -> None:
    from labs.lab_04.src.job_research import JobResearchModel, research_job_board
    from labs.lab_04.src.prompt_loader import load_task_prompt

    result = research_job_board(
        task_prompt=load_task_prompt(),
        skill_rules="Never invent candidate experience.",
        user_request=user_request,
        evidence_sources="- source_id=profile: Python API experience",
        model=JobResearchModel(Settings(model="offline-test", google_api_key="")),
    )

    assert result["status"] == "called"
    assert result["arguments"] == {"company": "stripe", "ats": "greenhouse", "limit": 3}


def test_offline_mcp_follow_up_ignores_non_company_references() -> None:
    from labs.lab_04.src.job_research import JobResearchModel, research_job_board
    from labs.lab_04.src.prompt_loader import load_task_prompt

    result = research_job_board(
        task_prompt=load_task_prompt(),
        skill_rules="Never invent candidate experience.",
        user_request=(
            "Earlier user requests:\n"
            "USER: Research current openings at Stripe.\n\n"
            "Latest user request:\nWhat about my resume?"
        ),
        evidence_sources="- source_id=profile: Python API experience",
        model=JobResearchModel(Settings(model="offline-test", google_api_key="")),
    )

    assert result["status"] == "skipped"
    assert result["arguments"] is None


def test_offline_mcp_decision_reads_company_after_the_openings_phrase() -> None:
    from labs.lab_04.src.job_research import JobResearchModel, research_job_board
    from labs.lab_04.src.prompt_loader import load_task_prompt

    result = research_job_board(
        task_prompt=load_task_prompt(),
        skill_rules="Never invent candidate experience.",
        user_request="Search for current openings at Stripe.",
        evidence_sources="- source_id=profile: Python API experience",
        model=JobResearchModel(Settings(model="offline-test", google_api_key="")),
    )

    assert result["arguments"] == {"company": "stripe", "ats": "greenhouse", "limit": 3}


@pytest.mark.parametrize(
    "user_request",
    ["List jobs at Stripe.", "What roles are available at Stripe?"],
)
def test_offline_mcp_decision_recognizes_job_and_role_requests(user_request: str) -> None:
    from labs.lab_04.src.job_research import JobResearchModel, research_job_board
    from labs.lab_04.src.prompt_loader import load_task_prompt

    result = research_job_board(
        task_prompt=load_task_prompt(),
        skill_rules="Never invent candidate experience.",
        user_request=user_request,
        evidence_sources="- source_id=profile: Python API experience",
        model=JobResearchModel(Settings(model="offline-test", google_api_key="")),
    )

    assert result["arguments"] == {"company": "stripe", "ats": "greenhouse", "limit": 3}


@pytest.mark.parametrize(
    "user_request",
    ["Is Stripe hiring?", "查看 Stripe 的在招职位"],
)
def test_offline_mcp_decision_parses_every_accepted_research_phrase(
    user_request: str,
) -> None:
    from labs.lab_04.src.job_research import JobResearchModel, research_job_board
    from labs.lab_04.src.prompt_loader import load_task_prompt

    result = research_job_board(
        task_prompt=load_task_prompt(),
        skill_rules="Never invent candidate experience.",
        user_request=user_request,
        evidence_sources="- source_id=profile: Python API experience",
        model=JobResearchModel(Settings(model="offline-test", google_api_key="")),
    )

    assert result["arguments"] == {"company": "stripe", "ats": "greenhouse", "limit": 3}


def test_mcp_selection_prompt_budgets_long_evidence_before_the_model_call() -> None:
    from labs.lab_04.src.job_research import (
        MODEL_INPUT_BUDGET_TOKENS,
        JobResearchModel,
        research_job_board,
    )
    from labs.lab_04.src.prompt_loader import load_task_prompt

    result = research_job_board(
        task_prompt=load_task_prompt(),
        skill_rules="Never invent candidate experience.",
        user_request="Verify this candidate claim against my existing materials.",
        evidence_sources=f"- source_id=profile: {'Python API experience. ' * 2_000}",
        model=JobResearchModel(Settings(model="offline-test", google_api_key="")),
    )

    decision_event = next(
        event for event in result["protocol_operations"] if event["operation"] == "select_mcp_tool"
    )
    budget = decision_event["details"]["input_budget"]
    assert budget["original_estimated_tokens"] > MODEL_INPUT_BUDGET_TOKENS
    assert budget["submitted_estimated_tokens"] <= MODEL_INPUT_BUDGET_TOKENS
    assert budget["evidence_truncated"] is True


def test_mcp_selection_budget_failure_names_the_selection_operation() -> None:
    from labs.lab_04.src.job_research import JobResearchModel, research_job_board
    from labs.lab_04.src.mcp_client_adapter import MCPBoundaryError
    from labs.lab_04.src.prompt_loader import LoadedTaskPrompt, REQUIRED_PLACEHOLDERS

    oversized_prompt = LoadedTaskPrompt(
        path="labs/lab_04/prompts/oversized-test.md",
        template="x" * 20_000 + "\n" + "\n".join(REQUIRED_PLACEHOLDERS),
        placeholders=list(REQUIRED_PLACEHOLDERS),
        estimated_tokens=5_000,
    )

    with pytest.raises(MCPBoundaryError) as excinfo:
        research_job_board(
            task_prompt=oversized_prompt,
            skill_rules="Never invent candidate experience.",
            user_request="Research current openings at Stripe on Greenhouse.",
            evidence_sources="",
            model=JobResearchModel(Settings(model="offline-test", google_api_key="")),
        )

    assert excinfo.value.operation == "select_mcp_tool"
    assert [event["operation"] for event in excinfo.value.completed_operations] == [
        "initialize",
        "tools/list",
    ]
    cause = excinfo.value.__cause__
    while isinstance(cause, BaseExceptionGroup):
        cause = cause.exceptions[0]
    assert isinstance(cause, ValueError)
    assert "task instructions exceed" in str(cause)


def test_course_mcp_path_does_not_force_a_tool_call_for_evidence_only_requests() -> None:
    from labs.lab_04.src.job_research import JobResearchModel, research_job_board
    from labs.lab_04.src.prompt_loader import load_task_prompt

    result = research_job_board(
        task_prompt=load_task_prompt(),
        skill_rules="Never invent candidate experience.",
        user_request="Verify this candidate claim against my existing materials.",
        evidence_sources="- source_id=profile: Python API experience",
        model=JobResearchModel(Settings(model="offline-test", google_api_key="")),
    )

    assert result["status"] == "skipped"
    assert result["called_tool"] is None
    assert [event["operation"] for event in result["protocol_operations"]] == [
        "initialize",
        "tools/list",
        "select_mcp_tool",
    ]


def test_tool_selection_preserves_the_provider_response_before_validation(monkeypatch) -> None:
    from types import SimpleNamespace

    import labs.lab_04.src.job_research as job_research
    from labs.shared.providers import ProviderToolDecision, provider_tool_request

    raw_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "No current-job lookup is needed.",
                }
            }
        ]
    }
    monkeypatch.setattr(
        job_research,
        "request_tool_decision",
        lambda *_: ProviderToolDecision(call=None, raw_response=raw_response),
    )
    settings = Settings(
        model="test-model",
        provider="openai",
        openai_api_key="test-key",
    )
    model = job_research.JobResearchModel(settings)
    tool = SimpleNamespace(
        name="list_openings",
        description="List current openings.",
        input_schema={
            "type": "object",
            "properties": {"company": {"type": "string"}},
            "required": ["company"],
        },
    )
    prompt = "Use the tool only when current openings are requested."

    assert model.decide(prompt, tool, "Verify my existing materials.") is None
    assert json.loads(model.io["raw_model_output"]) == raw_response
    assert json.loads(model.io["validated_output"]) == {"tool": None}
    assert json.loads(model.io["actual_provider_input"]) == provider_tool_request(
        settings,
        prompt,
        job_research._declaration(tool),
    )


def _live_job_research_model(monkeypatch, arguments: dict) -> object:
    import labs.lab_04.src.job_research as job_research
    from labs.shared.providers import ProviderToolCall, ProviderToolDecision

    monkeypatch.setattr(
        job_research,
        "request_tool_decision",
        lambda *_: ProviderToolDecision(
            call=ProviderToolCall(
                name="list_openings",
                arguments=arguments,
                continuation={},
            ),
            raw_response={"source": "test"},
        ),
    )
    return job_research.JobResearchModel(
        Settings(model="test-model", provider="openai", openai_api_key="test-key")
    )


def test_live_tool_selection_canonicalizes_job_board_arguments(monkeypatch) -> None:
    import labs.lab_04.src.job_research as job_research
    from labs.lab_04.src.prompt_loader import load_task_prompt

    result = job_research.research_job_board(
        task_prompt=load_task_prompt(),
        skill_rules="Never invent candidate experience.",
        user_request="Research current openings at Stripe on Greenhouse.",
        evidence_sources="- source_id=profile: Python API experience",
        model=_live_job_research_model(
            monkeypatch,
            {"company": " Stripe ", "ats": "Greenhouse", "limit": 3},
        ),
    )

    assert result["arguments"] == {"company": "stripe", "ats": "greenhouse", "limit": 3}
    assert len(result["records"]) == 3


def test_mcp_tool_error_preserves_the_server_message(monkeypatch) -> None:
    import labs.lab_04.src.job_research as job_research
    from labs.lab_04.src.mcp_client_adapter import MCPBoundaryError
    from labs.lab_04.src.prompt_loader import load_task_prompt

    with pytest.raises(MCPBoundaryError) as excinfo:
        job_research.research_job_board(
            task_prompt=load_task_prompt(),
            skill_rules="Never invent candidate experience.",
            user_request="Research current openings at Acme on Greenhouse.",
            evidence_sources="- source_id=profile: Python API experience",
            model=_live_job_research_model(
                monkeypatch,
                {"company": "acme", "ats": "greenhouse", "limit": 3},
            ),
        )

    assert "represents 'stripe', not 'acme'" in str(excinfo.value)
    assert "TaskGroup" not in str(excinfo.value)


def test_claim_prompt_includes_skill_rules_only_when_loaded(monkeypatch) -> None:
    from labs.shared.config import Settings
    from labs.shared.llm import LlmSession
    from labs.lab_04.src.claim_generation import ClaimGenerationModel

    seen: list[str] = []

    def capture(self, prompt, response_schema, offline_payload):
        seen.append(prompt)
        return [{"claim": "The candidate has Python API integration experience.", "source_id": "s1"}]

    monkeypatch.setattr(LlmSession, "complete_json", capture)
    sources = [SourceRecord(source_id="s1", title="Profile", path="p.json", snippet="Python API work.")]

    with_skill = ClaimGenerationModel(Settings(model="gemini-flash-latest", google_api_key="test-key"))
    with_skill.generate_claims(sources, skill_prompt="Follow these job-prep rules:\n- Never invent experience.\n")
    without_skill = ClaimGenerationModel(Settings(model="gemini-flash-latest", google_api_key="test-key"))
    without_skill.generate_claims(sources)

    assert "Never invent experience." in seen[0]
    assert "Never invent experience." not in seen[1]
    # The comparison must isolate the rules: everything else stays identical.
    assert seen[0].replace("Follow these job-prep rules:\n- Never invent experience.\n", "") == seen[1]


def test_offline_claims_keep_missing_requirements_and_opening_fit_unsupported() -> None:
    from labs.lab_04.src.claim_generation import fallback_claims

    sources = [
        SourceRecord(
            source_id="profile",
            title="Profile",
            path="profile.md",
            snippet="The candidate has Python API experience.",
        )
    ]

    missing_requirement = fallback_claims(
        sources,
        "The role requires Kubernetes but the profile does not mention it.",
    )[-1]
    opening_fit = fallback_claims(
        sources,
        "Research current openings at Stripe.",
    )[-1]

    assert missing_requirement == ClaimInput(
        claim="The candidate has production Kubernetes experience.",
        source_id=None,
    )
    assert opening_fit == ClaimInput(
        claim="The candidate meets every requirement of the returned openings.",
        source_id=None,
    )


def test_context_budget_keeps_drops_and_truncates_by_token_cost() -> None:
    from labs.lab_04.src.context_budget import (
        estimate_tokens,
        render_evidence_sources,
        select_context,
    )

    sources = [
        SourceRecord(source_id="a", title="A", path="a.json", snippet="x" * 80),  # 20 tokens
        SourceRecord(source_id="b", title="B", path="b.json", snippet="y" * 200),  # 50 tokens
        SourceRecord(source_id="c", title="C", path="c.json", snippet="z" * 200),  # 50 tokens
    ]

    selected = select_context(sources, budget_tokens=60, protected_tokens=10)

    # The rendered source_id prefixes and separators also spend the 50 tokens
    # left after protected content. "a" fits, "b" is truncated into the
    # remainder, and "c" is dropped rather than silently shortened.
    assert [source.source_id for source in selected.sources] == ["a", "b"]
    assert selected.truncated_source_ids == ["b"]
    assert selected.dropped_source_ids == ["c"]
    assert selected.estimated_tokens == 60
    assert selected.estimated_tokens == 10 + estimate_tokens(
        render_evidence_sources(selected.sources)
    )
    assert selected.sources[0].snippet == sources[0].snippet


def test_truncation_does_not_overshoot_the_budget_on_rounding() -> None:
    """Token estimates floor, so the block-so-far and the incoming record can
    each lose a fraction that together add up to a whole token. Measuring them
    separately then hands that token to the snippet and the reported budget
    ends up smaller than what actually renders.
    """
    from labs.lab_04.src.context_budget import (
        estimate_tokens,
        render_evidence_sources,
        select_context,
    )

    sources = [
        SourceRecord(source_id="aa", title="A", path="a.json", snippet="xx"),
        SourceRecord(source_id="bb", title="B", path="b.json", snippet="y" * 400),
    ]

    selected = select_context(sources, budget_tokens=18, protected_tokens=0)

    assert selected.estimated_tokens <= selected.budget_tokens
    assert selected.estimated_tokens == selected.protected_tokens + estimate_tokens(
        render_evidence_sources(selected.sources)
    )


def test_context_budget_counts_rendered_source_ids_and_separators() -> None:
    from labs.lab_04.src.context_budget import (
        estimate_tokens,
        render_evidence_sources,
        select_context,
    )

    sources = [
        SourceRecord(source_id=f"source-{index}", title="S", path="s.json", snippet="x" * 4)
        for index in range(3)
    ]
    rendered_tokens = estimate_tokens(render_evidence_sources(sources))
    raw_snippet_tokens = sum(estimate_tokens(source.snippet) for source in sources)

    assert rendered_tokens > raw_snippet_tokens

    selected = select_context(sources, budget_tokens=rendered_tokens - 1)

    assert selected.estimated_tokens <= selected.budget_tokens
    assert selected.dropped_source_ids


def test_context_budget_reserves_the_complete_claim_prompt_scaffold() -> None:
    from labs.lab_04.src.claim_generation import (
        claim_provider_input_tokens,
        claim_prompt_protected_tokens,
    )
    from labs.lab_04.src.context_budget import estimate_tokens, select_context

    skill_prompt = "Follow these job-prep rules:\n- Never invent experience.\n"
    sources = [
        SourceRecord(
            source_id="source-1",
            title="Profile",
            path="profile.md",
            snippet="Python API experience. " * 20,
        )
    ]
    protected_tokens = claim_prompt_protected_tokens(skill_prompt)
    selected = select_context(
        sources,
        budget_tokens=protected_tokens + 25,
        protected_tokens=protected_tokens,
        input_token_counter=lambda candidate_sources: claim_provider_input_tokens(
            candidate_sources,
            skill_prompt,
        ),
    )
    submitted_prompt_tokens = claim_provider_input_tokens(
        selected.sources,
        skill_prompt,
    )

    assert protected_tokens > estimate_tokens(skill_prompt)
    assert selected.estimated_tokens == submitted_prompt_tokens


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_claim_budget_measures_the_actual_structured_provider_envelope(provider: str) -> None:
    from labs.lab_04.src.claim_generation import (
        CLAIMS_RESPONSE_SCHEMA,
        claim_provider_input_tokens,
        claim_prompt_protected_tokens,
        render_claim_generation_prompt,
        render_claim_provider_input,
    )
    from labs.lab_04.src.context_budget import select_context
    from labs.shared.providers import provider_structured_request

    settings = Settings(model="test-model", provider=provider)
    skill_prompt = "Follow these job-prep rules:\n- Never invent experience.\n"
    sources = [
        SourceRecord(
            source_id="source-1",
            title="Profile",
            path="profile.md",
            snippet="Python API experience. " * 100,
        )
    ]
    protected_tokens = claim_prompt_protected_tokens(
        skill_prompt,
        settings=settings,
    )
    selected = select_context(
        sources,
        budget_tokens=protected_tokens + 50,
        protected_tokens=protected_tokens,
        input_token_counter=lambda candidate_sources: claim_provider_input_tokens(
            candidate_sources,
            skill_prompt,
            settings=settings,
        ),
    )
    prompt = render_claim_generation_prompt(selected.sources, skill_prompt)
    recorded_input = json.loads(render_claim_provider_input(prompt, settings))

    assert recorded_input == provider_structured_request(
        settings,
        prompt,
        CLAIMS_RESPONSE_SCHEMA,
    )
    assert selected.estimated_tokens == claim_provider_input_tokens(
        selected.sources,
        skill_prompt,
        settings=settings,
    )
    assert selected.estimated_tokens <= selected.budget_tokens


def test_claim_budget_reserves_the_structured_response_schema() -> None:
    from labs.lab_04.src.claim_generation import (
        CLAIMS_RESPONSE_SCHEMA,
        claim_prompt_protected_tokens,
        render_claim_generation_prompt,
        render_claim_provider_input,
    )
    from labs.lab_04.src.context_budget import estimate_tokens

    prompt = render_claim_generation_prompt([])
    provider_input = render_claim_provider_input(prompt)

    assert json.loads(provider_input)["config"]["response_schema"] == CLAIMS_RESPONSE_SCHEMA
    assert claim_prompt_protected_tokens() > estimate_tokens(prompt)


def test_context_budget_never_spends_protected_tokens_on_sources() -> None:
    from labs.lab_04.src.context_budget import select_context

    sources = [SourceRecord(source_id="a", title="A", path="a.json", snippet="x" * 400)]

    # The Skill rules alone exhaust the budget: evidence is dropped, and the
    # rules that govern how evidence must be used are not.
    selected = select_context(sources, budget_tokens=100, protected_tokens=100)

    assert selected.sources == []
    assert selected.dropped_source_ids == ["a"]
    assert selected.estimated_tokens == 100


def test_context_budget_rejects_protected_content_larger_than_budget() -> None:
    from labs.lab_04.src.context_budget import select_context

    with pytest.raises(ValueError, match="protected_tokens.*budget_tokens"):
        select_context([], budget_tokens=99, protected_tokens=100)


def test_dropped_evidence_turns_supported_claims_unsupported() -> None:
    from labs.lab_04.src.context_budget import select_context

    sources = [
        SourceRecord(
            source_id="source-profile-001",
            title="Profile",
            path="profile.json",
            snippet="The candidate has Python API integration experience.",
        )
    ]
    claims = [ClaimInput(claim=sources[0].snippet, source_id="source-profile-001")]

    assert build_evidence_notes(claims, sources)[0].status == "supported"

    selected = select_context(sources, budget_tokens=5)
    assert selected.dropped_source_ids == ["source-profile-001"]
    assert build_evidence_notes(claims, selected.sources)[0].status == "unsupported"


def test_mcp_boundary_is_a_real_protocol_session() -> None:
    result = ping_mcp_example()

    assert result["status"] == "ok"
    assert result["server"] == "job_prep_sources"
    # Negotiated by initialize, not hardcoded by the course.
    assert result["protocol_version"]
    assert result["capabilities"] == "tools/resources/prompts"
    assert result["tools"] == ["search_sources"]
    assert result["tool_count"] == "1"
    assert result["resources"] == ["job://candidate_profile", "job://job_description"]
    assert result["prompts"] == ["job_prep_report"]

    # The server declares a schema for the tool; that declaration is the
    # capability boundary the lab is about.
    schema = result["tool_input_schema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["query"]
    assert set(schema["properties"]) == {"query", "limit"}


def test_mcp_tool_call_crosses_the_boundary_with_lab_3_sources() -> None:
    from labs.lab_04.src.mcp_client_adapter import inspect_capability_boundary

    result = inspect_capability_boundary(
        query="Python",
        limit=2,
        source_items=[
            {"source_id": "source-a", "title": "A", "path": "a.json", "snippet": "Python API work."},
            {"source_id": "source-b", "title": "B", "path": "b.json", "snippet": "Python tooling."},
        ],
    )

    assert result["tool_error"] is False
    assert result["returned_source_ids"] == ["source-a", "source-b"]


def test_mcp_boundary_times_each_protocol_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    import anyio
    import labs.lab_04.src.mcp_client_adapter as mcp_adapter

    timestamps = iter([0.00, 0.01, 1.00, 1.02, 2.00, 2.03, 3.00, 3.04, 4.00, 4.05])

    class FakeClient:
        server_info = SimpleNamespace(name="job_prep_sources", version="test")
        protocol_version = "test-protocol"
        server_capabilities = SimpleNamespace(tools=object(), resources=object(), prompts=object())

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def list_tools(self):
            return SimpleNamespace(
                tools=[SimpleNamespace(name="search_sources", input_schema={"type": "object"})]
            )

        async def list_resources(self):
            return SimpleNamespace(resources=[SimpleNamespace(uri="job://candidate_profile")])

        async def list_prompts(self):
            return SimpleNamespace(prompts=[SimpleNamespace(name="job_prep_report")])

        async def call_tool(self, _name, _arguments):
            return SimpleNamespace(
                is_error=False,
                structured_content={"result": [{"source_id": "source-a"}]},
            )

    monkeypatch.setattr(mcp_adapter, "Client", lambda _server: FakeClient())
    monkeypatch.setattr(mcp_adapter, "perf_counter", lambda: next(timestamps))

    result = anyio.run(mcp_adapter._inspect, object(), "Python", 1)

    assert [item["operation"] for item in result["protocol_operations"]] == [
        "initialize",
        "tools/list",
        "resources/list",
        "prompts/list",
        "tools/call",
    ]
    assert [item["duration_ms"] for item in result["protocol_operations"]] == [10, 20, 30, 40, 50]
    assert result["protocol_operations"][1]["details"]["tool_input_schemas"] == {
        "search_sources": {"type": "object"}
    }
    assert result["protocol_operations"][-1]["details"]["arguments"] == {
        "query": "Python",
        "limit": 1,
    }


def test_mcp_failure_names_the_capability_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    import labs.lab_04.src.mcp_client_adapter as mcp_adapter

    class FailingClient:
        server_info = SimpleNamespace(name="job_prep_sources", version="test")
        protocol_version = "test-protocol"
        server_capabilities = SimpleNamespace(tools=object(), resources=object(), prompts=object())

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def list_tools(self):
            return SimpleNamespace(
                tools=[SimpleNamespace(name="search_sources", input_schema={"type": "object"})]
            )

        async def list_resources(self):
            raise RuntimeError("transport closed")

    monkeypatch.setattr(mcp_adapter, "Client", lambda _server: FailingClient())

    with pytest.raises(mcp_adapter.MCPBoundaryError) as excinfo:
        mcp_adapter.inspect_capability_boundary()

    assert excinfo.value.operation == "resources/list"
    assert [item["operation"] for item in excinfo.value.completed_operations] == [
        "initialize",
        "tools/list",
    ]
    assert web_adapter.failure_location(excinfo.value) == (
        "labs.lab_04.src.mcp_client_adapter.Client",
        "resources/list",
    )


def test_lab_4_failure_event_reports_the_mcp_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from labs.lab_04.src.mcp_client_adapter import MCPBoundaryError
    from labs.shared.web.contracts import ArtifactLink
    from labs.shared.web.errors import StageExecutionError

    def fail_at_mcp(**_):
        raise MCPBoundaryError("tools/list", RuntimeError("transport closed"))

    monkeypatch.setattr(web_adapter, "build_evidence_capability", fail_at_mcp)
    monkeypatch.setattr(
        web_adapter,
        "write_run_trace",
        lambda *_: ArtifactLink(label="Lab 04 call trace", path="artifacts/lab_04/trace.json"),
    )

    with pytest.raises(StageExecutionError) as excinfo:
        Lab04Adapter(MaterialStore(tmp_path / "materials")).chat(
            ChatRequest(
                stage="lab_04",
                session_id="session_mcp_failure",
                workspace_id="workspace_mcp_failure",
                messages=[{"role": "user", "content": "Inspect the MCP boundary."}],
            )
        )

    failure = excinfo.value.events[-1]
    assert failure.type == "capability_boundary"
    assert failure.component == "labs.lab_04.src.mcp_client_adapter.Client"
    assert failure.operation == "tools/list"


def test_completed_lab_4_events_survive_an_mcp_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import labs.lab_04.src.mcp_client_adapter as mcp_adapter

    class ClaimModel:
        mode = "fake"
        claim_source = "model"
        calls = 1
        last_io = {}

        def generate_claims(self, sources, skill_prompt="", request_context="", **_):
            return [ClaimInput(claim=sources[0].snippet, source_id=sources[0].source_id)]

    monkeypatch.setattr(
        web_adapter,
        "build_tool_action_capability",
        lambda **_: {
            "sources": [
                {
                    "source_id": "source-1",
                    "title": "Profile",
                    "path": "profile.md",
                    "snippet": "Python API experience.",
                }
            ],
            "tool_request": {"query": "Python API experience", "limit": 2},
            "request_context": "Latest user request:\nSummarize my fit for this role.",
            "material_ids": [],
            "profile": {},
            "job_description": {},
            "structured_report": {},
            "task_state": {},
            "action": {},
        },
    )
    monkeypatch.setattr(
        web_adapter,
        "load_student_skill",
        lambda: LoadedSkill(
            loaded=True,
            path="skill.md",
            name="job-prep",
            rules=["Never invent experience."],
            rule_count=1,
            estimated_tokens=10,
        ),
    )
    monkeypatch.setattr(
        "labs.lab_04.src.claim_generation.ClaimGenerationModel",
        ClaimModel,
    )
    monkeypatch.setattr(
        "labs.lab_04.src.job_research.research_job_board",
        lambda **_: (_ for _ in ()).throw(
            mcp_adapter.MCPBoundaryError(
                    "tools/call",
                RuntimeError("transport closed"),
                completed_operations=[
                    {
                        "operation": "initialize",
                        "summary": "Negotiated MCP protocol",
                        "duration_ms": 1,
                        "details": {"semantic_key": "capability.mcp.initialize"},
                    },
                    {
                        "operation": "tools/list",
                        "summary": "Discovered MCP tools",
                        "duration_ms": 2,
                        "details": {"semantic_key": "capability.mcp.tools.list"},
                    },
                ],
            )
        ),
    )

    events = []
    with pytest.raises(mcp_adapter.MCPBoundaryError):
        build_evidence_capability(
            materials=MaterialStore(tmp_path / "materials"),
            request=ChatRequest(
                stage="lab_04",
                session_id="session_mcp_events",
                workspace_id="workspace_mcp_events",
                messages=[{"role": "user", "content": "Inspect the MCP boundary."}],
            ),
            run_id="run_mcp_events",
            stage_id="lab_04",
            events=events,
            artifacts=[],
        )

    assert [event.operation for event in events] == [
        "load_skill",
        "load_task_prompt",
        "initialize",
        "tools/list",
    ]


class FakeClaimModel:
    """Offline stand-in for the Gemini claim generator. Tests never call the API."""

    mode = "fake"
    claim_source = "model"
    calls = 1
    last_io = {}

    def generate_claims(
        self,
        sources: list[SourceRecord],
        skill_prompt: str = "",
        request_context: str = "",
        **_,
    ) -> list[ClaimInput]:
        assert sources, "generate_claims should receive the retrieved sources"
        return [
            ClaimInput(claim=sources[0].snippet, source_id=sources[0].source_id),
            ClaimInput(claim="The candidate led a production multi-agent migration.", source_id=None),
        ]


def test_run_threads_model_claims_through_verifier(tmp_path: Path, monkeypatch) -> None:
    import labs.lab_04.src.run_evidence_report as run_evidence_report

    def fake_artifact_path(*parts: str) -> Path:
        path = tmp_path / Path(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(run_evidence_report, "artifact_path", fake_artifact_path)

    payload = run_evidence_report.run(use_skill=False, model=FakeClaimModel())

    assert payload["model"] == {
        "mode": "fake",
        "claim_source": "model",
        "model_calls": 1,
        "model_io": {},
    }
    statuses = {claim["claim"]: claim["status"] for claim in payload["claims"]}
    assert statuses["The candidate led a production multi-agent migration."] == "unsupported"
    assert "supported" in statuses.values()


def test_lab_3_request_context_reaches_the_claim_prompt_and_is_protected() -> None:
    """Lab 3 owns the bounded conversation context; Lab 4 must not drop it.

    Lab 3 already puts it in front of the Lab 2 model and Lab 6 threads it into
    both of its prompts. Lab 4 sits between them, so if its claim generator
    ignored it the report would answer a generic question rather than the one
    the candidate asked, and the budget would under-count the real prompt.
    """
    from labs.lab_04.src.claim_generation import (
        claim_prompt_protected_tokens,
        render_claim_generation_prompt,
    )

    request_context = (
        "Earlier user requests:\nUSER: Draft from my current evidence.\n\n"
        "Latest user request:\nNow focus on the API migration work."
    )
    sources = [SourceRecord(source_id="s1", title="Profile", path="p.json", snippet="Python API work.")]

    with_context = render_claim_generation_prompt(sources, "", request_context)
    without_context = render_claim_generation_prompt(sources, "", "")

    assert "Now focus on the API migration work." in with_context
    assert "Draft from my current evidence." in with_context
    assert "Now focus on the API migration work." not in without_context

    # Protected, not competing with evidence: the reservation grows by exactly
    # what the request added, so a tight budget still drops sources instead.
    assert claim_prompt_protected_tokens("", request_context) > claim_prompt_protected_tokens("")

    # No conversation (the CLI runner) leaves the prompt byte-for-byte as before.
    assert render_claim_generation_prompt(sources, "", "   ") == without_context


def test_lab_4_runs_against_an_already_installed_older_lab_3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Lab 4 zip carries no lab_03 file, so Lab 3 stays whatever the student
    already installed. An older Lab 3 adapter does not publish request_context,
    and Lab 4 must still complete rather than raise on a finished Lab.
    """
    def older_lab_3(*, materials, request, run_id, stage_id, events, artifacts):
        return {
            "sources": [
                {
                    "source_id": "source-profile-001",
                    "title": "Profile",
                    "path": "p.json",
                    "snippet": "Python API experience.",
                }
            ],
            "tool_request": {"query": "Python API experience", "limit": 2},
            # No "request_context": this is the shape shipped with Lab 3.
            "material_ids": [],
            "profile": {},
            "job_description": {},
            "structured_report": {},
            "task_state": {},
            "action": {},
        }

    seen: list[str] = []

    class RecordingClaimModel(FakeClaimModel):
        def generate_claims(self, sources, skill_prompt="", request_context="", **_):
            seen.append(request_context)
            return [ClaimInput(claim=sources[0].snippet, source_id=sources[0].source_id)]

    def fake_artifact_path(*parts: str) -> Path:
        path = tmp_path / Path(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(web_adapter, "build_tool_action_capability", older_lab_3)
    monkeypatch.setattr(
        "labs.lab_04.src.claim_generation.ClaimGenerationModel", RecordingClaimModel
    )
    monkeypatch.setattr(
        web_adapter,
        "load_student_skill",
        lambda: LoadedSkill(
            loaded=True,
            path="skill.md",
            name="job-prep",
            rules=["Never invent experience."],
            rule_count=1,
            estimated_tokens=8,
        ),
    )
    monkeypatch.setattr(web_adapter, "artifact_path", fake_artifact_path)
    monkeypatch.setattr(web_adapter, "relative_artifact_path", lambda path: "artifacts/report.json")

    events: list[HarnessEvent] = []
    payload = build_evidence_capability(
        materials=MaterialStore(tmp_path / "materials"),
        request=ChatRequest(
            stage="lab_04",
            session_id="session_old_lab3",
            workspace_id="workspace_old_lab3",
            messages=[{"role": "user", "content": "Summarize my fit."}],
        ),
        run_id="run_old_lab3",
        stage_id="lab_04",
        events=events,
        artifacts=[],
    )

    assert payload["claims"]
    assert seen == [""]
    budget = next(event for event in events if event.type == "context_budget")
    assert budget.details["request_context_tokens"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        [{"claim": "x", "source_id": 123}],  # wrong type: ValidationError
        [{"claim": "x"}],  # required source_id key missing: schema violation
        [{"claim": "", "source_id": "source-profile-001"}],  # empty claim text
        ["not-an-object"],  # item is not an object
    ],
)
def test_claim_generation_falls_back_on_invalid_model_payload(monkeypatch, payload) -> None:
    from labs.shared.config import Settings
    from labs.shared.llm import LlmSession
    from labs.lab_04.src.claim_generation import ClaimGenerationModel

    monkeypatch.setattr(
        LlmSession,
        "complete_json",
        lambda self, prompt, response_schema, offline_payload: payload,
    )
    model = ClaimGenerationModel(Settings(model="gemini-flash-latest", google_api_key="test-key"))

    claims = model.generate_claims(
        [SourceRecord(source_id="source-profile-001", title="Profile", path="p.json", snippet="Python API work.")]
    )

    assert model.claim_source == "fixture"
    assert claims
    assert all(isinstance(claim, ClaimInput) for claim in claims)
    assert json.loads(model.last_io["raw_model_output"]) == payload
    assert json.loads(model.last_io["validated_output"]) == [
        claim.model_dump() for claim in claims
    ]


def test_claims_response_schema_validates_as_sdk_schema() -> None:
    from google.genai import types
    from labs.lab_04.src.claim_generation import CLAIMS_RESPONSE_SCHEMA

    schema = types.Schema.model_validate(CLAIMS_RESPONSE_SCHEMA)

    assert schema.type == types.Type.ARRAY
    assert schema.items.type == types.Type.OBJECT
    assert schema.items.required == ["claim", "source_id"]
    assert schema.items.properties["claim"].type == types.Type.STRING


def test_claim_generation_accepts_empty_source_id_as_unsupported(monkeypatch) -> None:
    from labs.shared.config import Settings
    from labs.shared.llm import LlmSession
    from labs.lab_04.src.claim_generation import ClaimGenerationModel

    # An empty source_id is in-protocol: the prompt asks the model to mark
    # an unsupported claim that way. It must not trigger the fallback.
    monkeypatch.setattr(
        LlmSession,
        "complete_json",
        lambda self, prompt, response_schema, offline_payload: [
            {"claim": "The candidate has Python API integration experience.", "source_id": "source-profile-001"},
            {"claim": "The candidate will lead the platform team.", "source_id": ""},
        ],
    )
    model = ClaimGenerationModel(Settings(model="gemini-flash-latest", google_api_key="test-key"))

    claims = model.generate_claims(
        [SourceRecord(source_id="source-profile-001", title="Profile", path="p.json", snippet="Python API work.")]
    )

    assert model.claim_source == "model"
    assert claims[0].source_id == "source-profile-001"
    assert claims[1].source_id is None
    raw_output = json.loads(model.last_io["raw_model_output"])
    validated_output = json.loads(model.last_io["validated_output"])
    assert raw_output[1]["source_id"] == ""
    assert validated_output[1]["source_id"] is None


def test_lab_4_web_adapter_builds_current_run_sources_and_evidence_without_lab_3_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MaterialStore(tmp_path / "materials")
    workspace_id = "workspace_lab04test"
    profile = store.create_text(
        workspace_id,
        "candidate_profile",
        "profile.md",
        "CURRENT PROFILE: Candidate has production Go API integration experience.",
        source="fixture",
    )
    job_description = store.create_text(
        workspace_id,
        "job_description",
        "jd.md",
        "CURRENT JOB DESCRIPTION: Role requires Rust systems experience.",
        source="fixture",
    )
    store.create_text(
        workspace_id,
        "web_source",
        "Stale profile evidence",
        "STALE WEB SOURCE: Candidate has Python API integration experience.",
        source="fixture",
    )
    for index in range(3):
        store.create_text(
            workspace_id,
            "web_source",
            f"Additional evidence {index}",
            f"Python LLM API evidence from workspace source {index}.",
            source="fixture",
        )
    monkeypatch.setattr(
        ModelClient,
        "complete",
        lambda _self, _messages: (
            '{"fit_summary":"Partial fit","strengths":["Python"],'
            '"gaps":["Production agents"],"risks":["Unsupported claims"],'
            '"missing_info":["Scale"],"recommended_next_steps":["Collect evidence"]}'
        ),
    )
    monkeypatch.setattr(
        "labs.lab_03.src.model_tool_use.load_settings",
        lambda: Settings(model="offline-test", google_api_key=""),
    )
    monkeypatch.setattr(
        "labs.shared.llm.load_settings",
        lambda: Settings(model="offline-test", google_api_key=""),
    )

    response = Lab04Adapter(store).chat(
        ChatRequest(
            stage="lab_04",
            session_id="session_lab04test",
            workspace_id=workspace_id,
            messages=[
                {
                    "role": "user",
                    "content": "Verify that I led a production multi-agent migration.",
                }
            ],
        )
    )

    assert response.status == "ok"
    claims = response.state_summary["claims"]
    assert claims[0]["status"] == "supported"
    assert claims[-1]["status"] == "unsupported"
    assert claims[-1]["source_id"] is None
    assert [event.type for event in response.events] == [
        "state_load",
        "model_call",
        "validation",
        "state_update",
        "tool_call",
        "tool_result",
        "model_call",
        "guardrail",
        "state_update",
        "skill",
        "prompt",
        "capability_boundary",
        "capability_boundary",
        "model_call",
        "context_budget",
        "model_call",
        "evidence",
    ]
    tool_request = response.events[4].details["input"]
    tool_source_ids = response.events[5].details["source_ids"]
    assert len(tool_source_ids) == tool_request["limit"]
    evidence_source_ids = set(response.events[-1].details["source_ids"])
    assert set(tool_source_ids) <= evidence_source_ids
    assert {profile.material_id, job_description.material_id} <= evidence_source_ids
    provider_input = response.state_summary["model"]["model_io"]["actual_provider_input"]
    assert profile.material_id in provider_input
    assert job_description.material_id in provider_input
    assert "CURRENT PROFILE" in provider_input
    assert "CURRENT JOB DESCRIPTION" in provider_input
    assert [event.operation for event in response.events[-8:]] == [
        "load_skill",
        "load_task_prompt",
        "initialize",
        "tools/list",
        "select_mcp_tool",
        "select_context",
        "generate_claims",
        "build_evidence_notes",
    ]
    # Fixed prompt instructions and the Skill are both reserved before evidence
    # competes for the budget.
    assert response.events[-3].details["skill_tokens"] == response.events[-8].details[
        "estimated_tokens"
    ]
    assert response.events[-3].details["protected_tokens"] > response.events[-3].details[
        "skill_tokens"
    ]
    assert response.events[-3].details["prompt_scaffold_tokens"] > 0
    timed_operations = {
        event.operation: event.duration_ms
        for event in response.events
        if event.operation in {
            "load_skill",
            "load_task_prompt",
            "select_mcp_tool",
            "select_context",
            "generate_claims",
            "build_evidence_notes",
            "initialize",
            "tools/list",
        }
    }
    assert timed_operations.keys() == {
        "load_skill",
        "load_task_prompt",
        "select_mcp_tool",
        "select_context",
        "generate_claims",
        "build_evidence_notes",
        "initialize",
        "tools/list",
    }
    assert all(duration is not None and duration > 0 for duration in timed_operations.values())

    trace = normalize_trace("lab_04", response.run_id, response.events)
    semantic_keys = [span.semantic_key for span in trace.spans]
    assert semantic_keys.count("context.select_budgeted") == 1
    assert [key for key in semantic_keys if key.startswith("capability.mcp.")] == [
        "capability.mcp.initialize",
        "capability.mcp.tools.list",
    ]


def test_current_openings_example_calls_mcp_and_feeds_records_to_claim_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MaterialStore(tmp_path / "materials")
    workspace_id = "workspace_current_openings"
    store.create_defaults(workspace_id)
    monkeypatch.setattr(
        ModelClient,
        "complete",
        lambda _self, _messages: (
            '{"fit_summary":"Partial fit","strengths":["Python"],'
            '"gaps":["Current role details"],"risks":["Unsupported claims"],'
            '"missing_info":["Scale"],"recommended_next_steps":["Compare evidence"]}'
        ),
    )
    offline = Settings(model="offline-test", google_api_key="")
    monkeypatch.setattr("labs.lab_03.src.model_tool_use.load_settings", lambda: offline)
    monkeypatch.setattr("labs.shared.llm.load_settings", lambda: offline)
    monkeypatch.setattr("labs.lab_04.src.job_research.load_settings", lambda: offline)

    response = Lab04Adapter(store).chat(
        ChatRequest(
            stage="lab_04",
            session_id="session_current_openings",
            workspace_id=workspace_id,
            messages=[
                {
                    "role": "user",
                    "content": "Research current openings at Stripe on Greenhouse.",
                }
            ],
        )
    )

    assert response.state_summary["mcp_boundary"]["status"] == "called"
    assert response.state_summary["mcp_boundary"]["called_tool"] == "list_openings"
    assert response.state_summary["mcp_boundary"]["arguments"] == {
        "company": "stripe",
        "ats": "greenhouse",
        "limit": 3,
    }
    assert [
        event.operation
        for event in response.events
        if event.operation in {"initialize", "tools/list", "select_mcp_tool", "tools/call"}
    ] == ["initialize", "tools/list", "select_mcp_tool", "tools/call"]
    assert any(
        claim["source_id"] and claim["source_id"].startswith("job-greenhouse-")
        for claim in response.state_summary["claims"]
    )
    claim_input = response.state_summary["model"]["model_io"]["actual_provider_input"]
    assert "Account Executive, Bridge" in claim_input
    assert "list_openings" in claim_input


def test_current_opening_records_do_not_bypass_the_evidence_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from labs.lab_04.src.claim_generation import (
        claim_prompt_protected_tokens,
        render_claim_generation_prompt,
    )
    from labs.lab_04.src.context_budget import estimate_tokens
    from labs.lab_04.src.prompt_loader import load_task_prompt
    from labs.lab_04.src.skill_loader import skill_prompt_block

    request_context = "Latest user request:\nResearch current openings at Stripe."
    skill = LoadedSkill(
        loaded=True,
        path="skill.md",
        name="job-prep",
        rules=["Never invent experience."],
        rule_count=1,
        estimated_tokens=12,
    )
    task_prompt = load_task_prompt()
    opening_refs = [
        {"source_id": "job-greenhouse-keep"},
        {"source_id": "job-greenhouse-drop"},
    ]
    budget_tokens = claim_prompt_protected_tokens(
        skill_prompt_block(skill),
        request_context,
        task_prompt,
        [],
        opening_refs,
    ) + 40

    monkeypatch.setattr(web_adapter, "UI_CONTEXT_BUDGET_TOKENS", budget_tokens)
    monkeypatch.setattr(web_adapter, "load_student_skill", lambda: skill)
    monkeypatch.setattr(web_adapter, "artifact_path", lambda *_: tmp_path / "report.json")
    monkeypatch.setattr(
        web_adapter,
        "relative_artifact_path",
        lambda _path: "artifacts/report.json",
    )
    monkeypatch.setattr(
        web_adapter,
        "build_tool_action_capability",
        lambda **_: {
            "sources": [],
            "request_context": request_context,
            "material_ids": [],
            "profile": {},
            "job_description": {},
            "structured_report": {},
            "task_state": {},
            "action": {},
        },
    )

    dropped_marker = "RAW-DROPPED-OPENING-MUST-NOT-REACH-THE-MODEL"
    records = [
        {
            "ats": "greenhouse",
            "job_id": "keep",
            "title": "Keep role",
            "url": "https://example.com/keep",
            "location": "Remote",
            "department": "Engineering",
            "summary": "Python APIs. " * 400,
        },
        {
            "ats": "greenhouse",
            "job_id": "drop",
            "title": "Drop role",
            "url": "https://example.com/drop",
            "location": "Remote",
            "department": "Engineering",
            "summary": dropped_marker,
        },
    ]
    monkeypatch.setattr(
        "labs.lab_04.src.job_research.research_job_board",
        lambda **_: {
            "status": "called",
            "server": "job_board",
            "protocol_version": "test",
            "declared_tools": ["list_openings"],
            "tool_descriptors": [],
            "called_tool": "list_openings",
            "arguments": {"company": "stripe", "ats": "greenhouse", "limit": 3},
            "records": records,
            "protocol_operations": [],
            "model_io": {},
        },
    )

    class CapturingClaimModel:
        mode = "offline"
        claim_source = "fixture"
        calls = 0

        def __init__(self) -> None:
            self.last_io: dict = {}

        def generate_claims(self, sources, **values):
            prompt = render_claim_generation_prompt(sources, **values)
            self.last_io = {"actual_provider_input": prompt}
            return [ClaimInput(claim=sources[0].snippet, source_id=sources[0].source_id)]

    monkeypatch.setattr(
        "labs.lab_04.src.claim_generation.ClaimGenerationModel",
        CapturingClaimModel,
    )

    payload = build_evidence_capability(
        materials=MaterialStore(tmp_path / "materials"),
        request=ChatRequest(
            stage="lab_04",
            session_id="session_budgeted_openings",
            workspace_id="workspace_budgeted_openings",
            messages=[{"role": "user", "content": "Research current openings at Stripe."}],
        ),
        run_id="run_budgeted_openings",
        stage_id="lab_04",
        events=[],
        artifacts=[],
    )

    prompt = payload["model"]["model_io"]["actual_provider_input"]
    assert "job-greenhouse-drop" in payload["context_budget"]["dropped_source_ids"]
    assert dropped_marker in payload["mcp_boundary"]["records"][1]["summary"]
    assert dropped_marker not in prompt
    assert '"source_id": "job-greenhouse-keep"' in prompt
    assert '"source_id": "job-greenhouse-drop"' not in prompt
    assert '"job_id"' not in prompt
    assert estimate_tokens(prompt) <= budget_tokens


def test_claim_timing_excludes_model_construction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = 0.0

    def fake_perf_counter() -> float:
        return clock

    class TimedClaimModel:
        mode = "fake"
        claim_source = "model"
        calls = 1
        last_io = {}

        def __init__(self) -> None:
            nonlocal clock
            clock += 0.5

        def generate_claims(
            self,
            sources: list[SourceRecord],
            skill_prompt: str = "",
            request_context: str = "",
            **_,
        ) -> list[ClaimInput]:
            nonlocal clock
            clock += 0.002
            return [ClaimInput(claim=sources[0].snippet, source_id=sources[0].source_id)]

    monkeypatch.setattr(web_adapter, "perf_counter", fake_perf_counter)
    monkeypatch.setattr(web_adapter, "artifact_path", lambda *parts: tmp_path / "report.json")
    monkeypatch.setattr(web_adapter, "relative_artifact_path", lambda path: "artifacts/report.json")
    monkeypatch.setattr(
        web_adapter,
        "build_tool_action_capability",
        lambda **_: {
            "sources": [
                {
                    "source_id": "source-1",
                    "title": "Profile",
                    "path": "profile.md",
                    "snippet": "Python API experience.",
                }
            ],
            "tool_request": {"query": "Python API experience", "limit": 2},
            "request_context": "Latest user request:\nSummarize my fit for this role.",
            "material_ids": [],
            "profile": {},
            "job_description": {},
            "structured_report": {},
            "task_state": {},
            "action": {},
        },
    )
    monkeypatch.setattr(
        web_adapter,
        "load_student_skill",
        lambda: LoadedSkill(loaded=True, path="skill.md", rules=["Never invent experience."], rule_count=1),
    )
    monkeypatch.setattr(
        "labs.lab_04.src.claim_generation.ClaimGenerationModel",
        TimedClaimModel,
    )

    events = []
    build_evidence_capability(
        materials=MaterialStore(tmp_path / "materials"),
        request=ChatRequest(
            stage="lab_04",
            session_id="session_timing",
            workspace_id="workspace_timing",
            messages=[{"role": "user", "content": "Draft evidence."}],
        ),
        run_id="run_timing",
        stage_id="lab_04",
        events=events,
        artifacts=[],
    )

    generate_event = next(event for event in events if event.operation == "generate_claims")
    assert generate_event.duration_ms == 2


# --- The course-provided MCP client ------------------------------------------
# None of these tests reach the network: the job board server is built with the
# bundled fixture board, and the source server is in-process.


def declared_tools(server) -> list:
    import anyio
    from labs.lab_04.src.mcp_client import connect

    async def _list():
        async with connect(server) as client:
            return (await client.list_tools()).tools

    return anyio.run(_list)


def test_pick_tool_finds_declared_tool_and_names_alternatives() -> None:
    from labs.lab_04.src.job_board_server import build_job_board_server
    from labs.lab_04.src.mcp_client import ToolContractError, pick_tool

    tools = declared_tools(build_job_board_server(offline=True))

    assert pick_tool(tools, "list_openings").name == "list_openings"

    with pytest.raises(ToolContractError) as excinfo:
        pick_tool(tools, "search_jobs")
    # A client that guessed a name must be told what actually exists.
    assert "list_openings" in str(excinfo.value)
    assert "get_opening" in str(excinfo.value)


def test_build_arguments_uses_only_what_the_server_declared() -> None:
    from labs.lab_04.src.mcp_client import ToolContractError, build_arguments

    schema = {
        "type": "object",
        "properties": {"company": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["company"],
    }

    # Undeclared keys are dropped rather than sent and hoped for.
    assert build_arguments(schema, {"company": "stripe", "limit": 5, "sort": "recent"}) == {
        "company": "stripe",
        "limit": 5,
    }

    with pytest.raises(ToolContractError):
        build_arguments(schema, {"limit": 5})


def test_same_client_drives_two_different_servers() -> None:
    """The point of the protocol: the client is not written against one server."""
    import anyio
    from labs.lab_04.src.job_board_server import build_job_board_server
    from labs.lab_04.src.mcp_client import call_discovered_tool, connect, records_from

    async def call(server, wanted, values):
        async with connect(server) as client:
            return records_from(await call_discovered_tool(client, wanted, values))

    from labs.lab_04.src.mcp_client_adapter import build_source_server

    sources = anyio.run(
        call,
        build_source_server(
            [{"source_id": "source-a", "title": "A", "path": "a.json", "snippet": "Python API work."}]
        ),
        "search_sources",
        {"query": "Python", "limit": 1},
    )
    openings = anyio.run(
        call,
        build_job_board_server(offline=True),
        "list_openings",
        {"company": "stripe", "ats": "greenhouse", "limit": 2},
    )

    assert sources[0]["source_id"] == "source-a"
    assert len(openings) == 2
    assert openings[0]["title"]
    assert openings[0]["url"].startswith("http")


def test_job_board_client_cli_exposes_the_short_record_limit() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "labs.lab_04.src.mcp_client",
            "--server",
            "jobs",
            "--offline",
            "--limit",
            "1",
        ],
        cwd=Path(__file__).resolve().parents[3],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    assert result.returncode == 0
    assert "records=1" in result.stdout


def test_job_board_server_answers_over_stdio_as_a_subprocess() -> None:
    """The optional "register this server in Claude Code / Codex" step is stdio.

    Everything else in the lab drives an in-process server, so nothing else
    would notice if the stdio entry point stopped launching. This runs the real
    `--offline` subprocess: no network, no key, and it fails on the platforms
    where subprocess launching differs rather than only on the maintainer's Mac.
    """
    import sys

    import anyio
    from mcp.client import Client
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def call() -> tuple[str, list[str], list[dict]]:
        from labs.lab_04.src.mcp_client import call_discovered_tool, records_from

        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "labs.lab_04.src.job_board_server", "--offline"],
            cwd=str(Path(__file__).resolve().parents[3]),
        )
        async with Client(stdio_client(parameters)) as client:
            listing = await client.list_tools()
            result = await call_discovered_tool(
                client,
                "list_openings",
                {"company": "stripe", "ats": "greenhouse", "limit": 2},
            )
            return client.server_info.name, [tool.name for tool in listing.tools], records_from(result)

    name, tools, openings = anyio.run(call)

    assert name == "job_board"
    assert {"list_openings", "get_opening"} <= set(tools)
    assert len(openings) == 2
    assert openings[0]["title"]


def test_job_board_normalizes_three_ats_shapes_into_one_contract() -> None:
    from labs.lab_04.src.job_board_server import JobOpening, load_fixture_payload, normalize

    for ats in ("greenhouse", "lever", "ashby"):
        records = normalize(load_fixture_payload(ats), ats, "demo")

        assert records, f"{ats} fixture produced no records"
        for opening, body in records:
            assert isinstance(opening, JobOpening)
            assert opening.job_id and opening.title
            assert opening.url.startswith("http")
            assert opening.ats == ats
            # list results stay short; the full body is what get_opening returns
            assert len(opening.summary) <= 400
            assert len(body) >= len(opening.summary)


@pytest.mark.parametrize(
    ("ats", "fixture_company"),
    [("greenhouse", "stripe"), ("lever", "spotify"), ("ashby", "ramp")],
)
def test_offline_job_board_rejects_a_company_not_in_the_fixture(
    ats: str,
    fixture_company: str,
) -> None:
    from labs.lab_04.src.job_board_server import JobBoardError, load_fixture_payload

    with pytest.raises(JobBoardError) as excinfo:
        load_fixture_payload(ats, "acme")

    assert f"represents {fixture_company!r}, not 'acme'" in str(excinfo.value)


def test_job_board_only_builds_urls_for_allowlisted_hosts() -> None:
    from labs.lab_04.src.job_board_server import ATS_HOSTS, JobBoardError, board_url

    assert board_url("greenhouse", "stripe").startswith(ATS_HOSTS["greenhouse"])
    assert board_url("lever", "spotify").startswith(ATS_HOSTS["lever"])

    # A caller cannot steer this server at a host of their choosing.
    for ats, company in [
        ("greenhouse", "../../etc/passwd"),
        ("greenhouse", "evil.example.com"),
        ("greenhouse", "Stripe"),
        ("wonderland", "stripe"),
    ]:
        with pytest.raises(JobBoardError):
            board_url(ats, company)


def test_get_opening_returns_the_full_description_for_one_job() -> None:
    import anyio
    from labs.lab_04.src.job_board_server import build_job_board_server
    from labs.lab_04.src.mcp_client import call_discovered_tool, connect, records_from

    server = build_job_board_server(offline=True)

    async def call(wanted, values):
        async with connect(server) as client:
            return records_from(await call_discovered_tool(client, wanted, values))

    listed = anyio.run(call, "list_openings", {"company": "stripe", "limit": 1})[0]
    detail = anyio.run(call, "get_opening", {"company": "stripe", "job_id": listed["job_id"]})[0]

    assert detail["job_id"] == listed["job_id"]
    # Progressive disclosure: the detail call is what carries the full text.
    assert len(detail["description"]) >= len(listed["summary"])
