"""Course-owned model-to-MCP job research path used by the Lab 4 web run."""

from __future__ import annotations

import json
import re
from time import perf_counter
from typing import Any

import anyio
from mcp import Client

from labs.lab_04.src.context_budget import CHARS_PER_TOKEN
from labs.lab_04.src.job_board_server import FIXTURE_COMPANIES, build_job_board_server
from labs.lab_04.src.mcp_client import build_arguments, records_from
from labs.lab_04.src.mcp_client_adapter import MCPBoundaryError
from labs.lab_04.src.prompt_loader import LoadedTaskPrompt, render_task_prompt
from labs.shared.config import Settings, load_settings
from labs.shared.providers import provider_tool_request, request_tool_decision


RESEARCH_TERMS = (
    "current opening",
    "open role",
    "openings",
    "jobs",
    "roles",
    "hiring",
    "在招",
    "职位",
    "岗位",
)
DEFAULT_ARGUMENTS = {"ats": "greenhouse", "limit": 3}
OFFLINE_FIXTURE_ATS = {company: ats for ats, company in FIXTURE_COMPANIES.items()}
COMPANY_REFERENCE_WORDS = {
    "a",
    "an",
    "at",
    "for",
    "from",
    "it",
    "me",
    "my",
    "our",
    "the",
    "this",
    "us",
}
MODEL_INPUT_BUDGET_TOKENS = 4_000


def _duration_ms(started: float) -> int:
    return max(1, round((perf_counter() - started) * 1000))


def _sdk_schema(value: Any) -> Any:
    """Translate JSON Schema type names to the Gemini SDK declaration form."""
    if isinstance(value, list):
        return [_sdk_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    converted = {key: _sdk_schema(item) for key, item in value.items()}
    if isinstance(converted.get("type"), str):
        converted["type"] = converted["type"].upper()
    return converted


def _declaration(tool: Any) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description or "",
        "parameters": _sdk_schema(tool.input_schema),
    }


def _provider_input(
    settings: Settings,
    prompt: str,
    declaration: dict[str, Any],
) -> str:
    return json.dumps(
        provider_tool_request(settings, prompt, declaration),
        indent=2,
        ensure_ascii=False,
    )


def _estimated_tokens(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def _bounded_selection_prompt(
    *,
    task_prompt: LoadedTaskPrompt,
    skill_rules: str,
    user_request: str,
    evidence_sources: str,
    tool_descriptors: list[dict[str, Any]],
    declaration: dict[str, Any],
    settings: Settings,
    budget_tokens: int = MODEL_INPUT_BUDGET_TOKENS,
) -> tuple[str, dict[str, Any]]:
    """Fit evidence into the real model input used to choose an MCP tool."""

    def render(evidence: str) -> str:
        return render_task_prompt(
            task_prompt,
            skill_rules=skill_rules,
            user_request=user_request,
            evidence_sources=evidence,
            available_tools=tool_descriptors,
            job_openings=[],
        )

    def input_tokens(evidence: str) -> int:
        return _estimated_tokens(_provider_input(settings, render(evidence), declaration))

    original_tokens = input_tokens(evidence_sources)
    protected_tokens = input_tokens("")
    if protected_tokens > budget_tokens:
        raise ValueError("Lab 4 task instructions exceed the model-input budget")

    submitted_evidence = evidence_sources
    if original_tokens > budget_tokens:
        low = 0
        high = len(evidence_sources)
        while low < high:
            midpoint = (low + high + 1) // 2
            if input_tokens(evidence_sources[:midpoint]) <= budget_tokens:
                low = midpoint
            else:
                high = midpoint - 1
        submitted_evidence = evidence_sources[:low]

    prompt = render(submitted_evidence)
    return prompt, {
        "budget_tokens": budget_tokens,
        "protected_tokens": protected_tokens,
        "original_estimated_tokens": original_tokens,
        "submitted_estimated_tokens": _estimated_tokens(
            _provider_input(settings, prompt, declaration)
        ),
        "evidence_characters": len(evidence_sources),
        "submitted_evidence_characters": len(submitted_evidence),
        "evidence_truncated": submitted_evidence != evidence_sources,
    }


def _offline_fixture_company(value: str) -> str | None:
    if value in COMPANY_REFERENCE_WORDS or value not in OFFLINE_FIXTURE_ATS:
        return None
    return value


def _company_from_research_request(request: str) -> str | None:
    for preposition in ("at", "from", "for"):
        matches = re.findall(
            rf"\b{preposition}\s+([a-z0-9-]{{2,63}})\b",
            request,
        )
        for value in reversed(matches):
            company = _offline_fixture_company(value)
            if company is not None:
                return company

    for pattern in (
        r"\b([a-z0-9-]{2,63})\s+(?:current\s+)?(?:openings|jobs|roles)\b",
        r"\b(?:is\s+)?([a-z0-9-]{2,63})\s+(?:is\s+)?hiring\b",
        r"\b([a-z0-9-]{2,63})\s*的?\s*(?:在招|职位|岗位)",
    ):
        company_match = re.search(pattern, request)
        if company_match is not None:
            company = _offline_fixture_company(company_match.group(1))
            if company is not None:
                return company
    return None


def _follow_up_company(request: str) -> str | None:
    company_match = re.search(
        r"\b(?:what|how)\s+about\s+([a-z0-9-]{2,63})\b",
        request,
    )
    if company_match is None:
        company_match = re.search(
            r"(?:那|那么)?\s*\b([a-z0-9-]{2,63})\b\s*(?:呢|怎么样)",
            request,
        )
    if company_match is None:
        return None
    return _offline_fixture_company(company_match.group(1))


def _offline_decision(user_request: str) -> dict[str, Any] | None:
    lowered = user_request.lower()
    latest_request_marker = "latest user request:"
    prior_request = ""
    if latest_request_marker in lowered:
        history, lowered = lowered.rsplit(latest_request_marker, 1)
        prior_requests = re.findall(r"(?m)^user:\s*(.+)$", history)
        if prior_requests:
            prior_request = prior_requests[-1]

    has_research_intent = any(term in lowered for term in RESEARCH_TERMS)
    company = _company_from_research_request(lowered) if has_research_intent else None
    if company is None and any(term in prior_request for term in RESEARCH_TERMS):
        company = _follow_up_company(lowered)
    if company is None:
        return None

    arguments = dict(DEFAULT_ARGUMENTS)
    arguments["company"] = company
    arguments["ats"] = OFFLINE_FIXTURE_ATS.get(company, arguments["ats"])
    for ats in ("greenhouse", "lever", "ashby"):
        if ats in lowered:
            arguments["ats"] = ats
            break
    return arguments


class JobResearchModel:
    """Let the selected model decide whether/how to call `list_openings`."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self.decision_source: str | None = None
        self.io: dict[str, Any] = {}

    @property
    def live(self) -> bool:
        return bool(self.settings.api_key)

    @property
    def mode(self) -> str:
        return "live" if self.live else "offline"

    def decide(self, prompt: str, tool: Any, user_request: str) -> dict[str, Any] | None:
        declaration = _declaration(tool)
        raw: Any

        if not self.live:
            arguments = _offline_decision(user_request)
            self.decision_source = "offline_fallback"
            raw = {"source": self.decision_source, "tool": "list_openings" if arguments else None}
        elif self.settings.provider != "gemini":
            decision = request_tool_decision(self.settings, prompt, declaration)
            call = decision.call
            arguments = None if call is None else dict(call.arguments)
            self.decision_source = "model"
            raw = decision.raw_response
        else:
            try:
                from google import genai
                from google.genai import types
            except ImportError as exc:
                raise RuntimeError("google-genai is required. Run `uv sync` first.") from exc
            client = genai.Client(api_key=self.settings.google_api_key)
            config = types.GenerateContentConfig(
                tools=[types.Tool(function_declarations=[declaration])]
            )
            response = client.models.generate_content(
                model=self.settings.model,
                contents=prompt,
                config=config,
            )
            raw = response.model_dump(mode="json", exclude_none=True)
            function_call = next(
                (
                    part.function_call
                    for part in (response.candidates[0].content.parts or [])
                    if part.function_call and part.function_call.name == "list_openings"
                ),
                None,
            )
            arguments = None if function_call is None else dict(function_call.args or {})
            self.decision_source = "model"

        if arguments is not None:
            arguments = {
                **DEFAULT_ARGUMENTS,
                **arguments,
            }
            for field in ("company", "ats"):
                if isinstance(arguments.get(field), str):
                    arguments[field] = arguments[field].strip().lower()
            arguments["limit"] = max(1, min(int(arguments.get("limit", 3)), 5))
            arguments = build_arguments(tool.input_schema, arguments)

        self.io = {
            "provider": self.settings.provider,
            "model": self.settings.model,
            "actual_provider_input": _provider_input(self.settings, prompt, declaration),
            "raw_model_output": json.dumps(raw, indent=2, ensure_ascii=False),
            "validated_output": json.dumps(
                {"tool": "list_openings", "arguments": arguments} if arguments else {"tool": None},
                indent=2,
                ensure_ascii=False,
            ),
        }
        return arguments


async def _research(
    *,
    task_prompt: LoadedTaskPrompt,
    skill_rules: str,
    user_request: str,
    evidence_sources: str,
    model: JobResearchModel,
) -> dict[str, Any]:
    operations: list[dict[str, Any]] = []
    operation = "initialize"
    try:
        started = perf_counter()
        async with Client(build_job_board_server(offline=True)) as client:
            operations.append(
                {
                    "event_type": "capability_boundary",
                    "component": "labs.lab_04.src.mcp_client.Client",
                    "operation": operation,
                    "duration_ms": _duration_ms(started),
                    "summary": f"Negotiated MCP protocol {client.protocol_version} with job_board",
                    "details": {
                        "semantic_key": "capability.mcp.initialize",
                        "protocol_version": str(client.protocol_version),
                        "transport": "in_process",
                        "data_mode": "bundled_fixture",
                    },
                }
            )

            operation = "tools/list"
            started = perf_counter()
            listing = await client.list_tools()
            list_tool = next(tool for tool in listing.tools if tool.name == "list_openings")
            tool_descriptors = [
                {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
                for tool in listing.tools
            ]
            operations.append(
                {
                    "event_type": "capability_boundary",
                    "component": "labs.lab_04.src.mcp_client.Client",
                    "operation": operation,
                    "duration_ms": _duration_ms(started),
                    "summary": f"Discovered {len(listing.tools)} job-board tool declarations",
                    "details": {
                        "semantic_key": "capability.mcp.tools.list",
                        "tools": tool_descriptors,
                    },
                }
            )

            operation = "select_mcp_tool"
            declaration = _declaration(list_tool)
            prompt, input_budget = _bounded_selection_prompt(
                task_prompt=task_prompt,
                skill_rules=skill_rules,
                user_request=user_request,
                evidence_sources=evidence_sources,
                tool_descriptors=tool_descriptors,
                declaration=declaration,
                settings=model.settings,
            )
            started = perf_counter()
            arguments = model.decide(prompt, list_tool, user_request)
            operations.append(
                {
                    "event_type": "model_call",
                    "component": "labs.lab_04.src.job_research.JobResearchModel",
                    "operation": operation,
                    "duration_ms": _duration_ms(started),
                    "summary": (
                        "Selected list_openings for current-job research"
                        if arguments
                        else "Skipped job-board call because the request did not need current openings"
                    ),
                    "details": {
                        "semantic_key": "model.select_mcp_tool",
                        "mode": model.mode,
                        "decision_source": model.decision_source,
                        "input_budget": input_budget,
                        "model_io": model.io,
                    },
                }
            )

            if arguments is None:
                return {
                    "status": "skipped",
                    "server": client.server_info.name,
                    "protocol_version": str(client.protocol_version),
                    "declared_tools": [tool.name for tool in listing.tools],
                    "tool_descriptors": tool_descriptors,
                    "called_tool": None,
                    "arguments": None,
                    "records": [],
                    "protocol_operations": operations,
                    "model_io": model.io,
                }

            operation = "tools/call"
            started = perf_counter()
            result = await client.call_tool("list_openings", arguments)
            records = records_from(result)
            operations.append(
                {
                    "event_type": "capability_boundary",
                    "component": "labs.lab_04.src.mcp_client.Client",
                    "operation": operation,
                    "duration_ms": _duration_ms(started),
                    "summary": f"Called list_openings and received {len(records)} short records",
                    "details": {
                        "semantic_key": "capability.mcp.tools.call",
                        "tool": "list_openings",
                        "arguments": arguments,
                        "content_block_types": [block.type for block in result.content],
                        "structured_content_present": result.structured_content is not None,
                        "record_count": len(records),
                    },
                }
            )
            return {
                "status": "called",
                "server": client.server_info.name,
                "protocol_version": str(client.protocol_version),
                "declared_tools": [tool.name for tool in listing.tools],
                "tool_descriptors": tool_descriptors,
                "called_tool": "list_openings",
                "arguments": arguments,
                "records": records,
                "protocol_operations": operations,
                "model_io": model.io,
            }
    except Exception as exc:
        cause = exc
        while isinstance(cause, BaseExceptionGroup) and cause.exceptions:
            cause = cause.exceptions[0]
        if isinstance(cause, MCPBoundaryError):
            raise cause from exc
        raise MCPBoundaryError(
            operation,
            cause,
            completed_operations=operations,
            component="labs.lab_04.src.mcp_client.Client",
        ) from exc


def research_job_board(
    *,
    task_prompt: LoadedTaskPrompt,
    skill_rules: str,
    user_request: str,
    evidence_sources: str,
    model: JobResearchModel | None = None,
) -> dict[str, Any]:
    selected_model = model or JobResearchModel()

    async def run() -> dict[str, Any]:
        return await _research(
            task_prompt=task_prompt,
            skill_rules=skill_rules,
            user_request=user_request,
            evidence_sources=evidence_sources,
            model=selected_model,
        )

    return anyio.run(run)
