from __future__ import annotations

from labs.lab_03.src.tools import search_sources
from labs.lab_06 import model
from labs.lab_06.contracts import AgentContract, AgentInstruction


INSTRUCTION = AgentInstruction(
    role="Research the bounded job-prep request using the existing local source capability.",
    objective="Plan one concise search query and return complete source records, ids, and snippets.",
    boundary="Use only the supplied source items and search tool; do not invent facts or perform actions.",
)

CONTRACT = AgentContract(
    input_fields=["query", "search_query", "source_items"],
    output_fields=["search_query", "sources", "source_ids", "source_snippets"],
    failure_statuses=["invalid_input", "model_failed", "tool_failed"],
    trace_events=["model_call", "tool_call", "tool_result"],
)


def render_research_prompt(query: str) -> str:
    return (
        "Turn the bounded user request below into one concise search query for the "
        "existing local source tool. Return only the query text.\n\n"
        f"Bounded user request:\n{query}"
    )


def plan_query(input_data: dict) -> str:
    return model.traced_complete(
        agent="research",
        operation="plan_search_query",
        semantic_key="model.plan_search_query",
        prompt=render_research_prompt(input_data["query"]),
        offline_text=input_data["query"],
        instruction=INSTRUCTION,
    ).strip()


def run(input_data: dict) -> dict:
    sources = search_sources(
        input_data["search_query"],
        limit=input_data.get("limit", 3),
        source_items=input_data["source_items"],
    )
    source_records = [source.model_dump() for source in sources]
    return {
        "search_query": input_data["search_query"],
        "sources": source_records,
        "source_ids": [source.source_id for source in sources],
        "source_snippets": [source.snippet for source in sources],
    }
