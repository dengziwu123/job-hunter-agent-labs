from __future__ import annotations

import argparse
import json
from typing import Any

from labs.shared.artifacts import artifact_path
from labs.lab_03.src.model_tool_use import ToolUseModel
from labs.lab_03.src.policies import draft_action
from labs.lab_03.src.tools import search_sources


USER_REQUEST = (
    "Draft a short outreach email to the hiring manager for the AI Tools "
    "Engineer role, grounded in my local sources, then send it for me."
)
UNSAFE_SEND_REQUEST = "Send this outreach email to the hiring manager without asking me again."


def write_trace_event(event: dict[str, Any]) -> None:
    path = artifact_path("lab_03", "tool_trace.jsonl")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def run_demo(model: ToolUseModel | None = None) -> None:
    model = model or ToolUseModel()

    trace_path = artifact_path("lab_03", "tool_trace.jsonl")
    if trace_path.exists():
        trace_path.unlink()

    # Phase 1: the model decides how to call the local tool.
    tool_request = model.request_tool_call(USER_REQUEST)
    write_trace_event(
        {
            "event": "tool_call",
            "tool": "search_sources",
            "mode": model.mode,
            "source": model.tool_call_source,
            "input": tool_request,
        }
    )
    sources = search_sources(tool_request["query"], limit=tool_request.get("limit") or 2)
    write_trace_event(
        {
            "event": "tool_result",
            "tool": "search_sources",
            "result_count": len(sources),
            "source_ids": [source.source_id for source in sources],
        }
    )

    # Phase 2: the model drafts the outreach text from the tool results.
    draft_text = model.draft_outreach(USER_REQUEST, sources)
    draft = draft_action("outreach_draft", draft_text)
    write_trace_event(
        {
            "event": "draft_action",
            "action_type": draft.action_type,
            "status": draft.status,
            "reason": draft.reason,
            "content": draft.content,
        }
    )

    # Phase 3: the send request itself crosses the action boundary.
    action = draft_action("send_email", UNSAFE_SEND_REQUEST)
    write_trace_event(
        {
            "event": "draft_action",
            "action_type": action.action_type,
            "status": action.status,
            "reason": action.reason,
        }
    )
    write_trace_event(
        {
            "event": "approval_decision",
            "action_type": action.action_type,
            "status": action.status,
        }
    )

    print("tool=search_sources")
    print(f"mode={model.mode}")
    print(f"tool_call_source={model.tool_call_source}")
    print(f"model_calls={model.calls}")
    print(f"draft_status={draft.status}")
    print(f"action_status={action.status}")
    print("trace=artifacts/lab_03/tool_trace.jsonl")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()

    if not args.demo:
        raise SystemExit("Use --demo for the Lab 3 starter flow.")

    run_demo()


if __name__ == "__main__":
    main()
