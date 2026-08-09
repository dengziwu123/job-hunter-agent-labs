"""An MCP client that discovers what a server offers instead of assuming it.

In Lab 3 you called `search_sources()` because it was in scope: you already knew
its name and its parameters. A client cannot work that way. It connects to a
server it did not write, asks `tools/list` what exists, reads the schema the
server declares, and builds the call from that answer.

That is the whole difference, and it is why the same client in this file can
talk to both Lab 4 servers without a single branch:

    job_prep_sources -> search_sources(query, limit)
    job_board        -> list_openings(company, ats, limit)

If your client only works against one of them, it is not reading the
declaration — it is hardcoding an assumption that happens to hold.

The whole client is course plumbing in this Lab. Read it alongside the handout:
students author the prompt that guides tool use, not protocol glue.
"""

from __future__ import annotations

import argparse
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import anyio
from mcp import Client
from mcp.types import CallToolResult, Tool


class ToolContractError(RuntimeError):
    """The server does not offer what the caller asked for."""


def pick_tool(tools: list[Tool], wanted: str) -> Tool:
    """Find `wanted` among the tools the server declared in `tools/list`."""
    for tool in tools:
        if tool.name == wanted:
            return tool

    available = ", ".join(sorted(tool.name for tool in tools)) or "none"
    raise ToolContractError(f"Server does not declare {wanted!r}. Available tools: {available}.")


def build_arguments(schema: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Build a call payload that matches the schema the server published.

    `schema` is the tool's `input_schema`: a JSON Schema object with
    `properties` and, usually, `required`.
    """
    declared = schema.get("properties", {})
    arguments = {name: value for name, value in values.items() if name in declared}

    missing = [name for name in schema.get("required", []) if name not in arguments]
    if missing:
        raise ToolContractError(
            f"Missing required argument(s): {', '.join(sorted(missing))}. "
            f"The server declares: {', '.join(sorted(declared)) or 'none'}."
        )
    return arguments


@asynccontextmanager
async def connect(server: Any) -> AsyncIterator[Client]:
    """Open an MCP session.

    `server` is an in-process `MCPServer`, an HTTP URL string, or a transport.
    To reach a server running as a separate process, wrap the command:
    `connect(stdio_client(StdioServerParameters(command=..., args=[...])))`.
    A bare command string does not work — it is read as an HTTP URL.
    """
    async with Client(server) as client:
        yield client


async def call_discovered_tool(
    client: Client,
    wanted: str,
    values: dict[str, Any],
) -> CallToolResult:
    """Discover -> read the schema -> build the call -> invoke it."""
    listing = await client.list_tools()
    tool = pick_tool(listing.tools, wanted)
    arguments = build_arguments(tool.input_schema, values)
    return await client.call_tool(tool.name, arguments)


def records_from(result: CallToolResult) -> list[dict]:
    """Pull records out of a tool result.

    A tool result is a list of content blocks. `structured_content` is an
    optional convenience the server may also fill in — the job board server
    provides it for `list_openings` but not for `get_opening`, because the SDK
    only emits it when it can infer a schema from the return annotation. A
    client that reads only `structured_content` silently gets nothing back from
    half the tools it calls, so handle both.
    """
    if result.is_error:
        details = [block.text for block in result.content if getattr(block, "text", None)]
        message = details[0] if details else "The server returned an error result."
        raise ToolContractError(message)

    structured = result.structured_content or {}
    if structured:
        payload = structured.get("result", structured)
    else:
        texts = [block.text for block in result.content if getattr(block, "text", None)]
        if not texts:
            return []
        payload = json.loads(texts[0])

    return [payload] if isinstance(payload, dict) else list(payload)


async def run(
    server_kind: str,
    company: str,
    ats: str,
    offline: bool,
    limit: int = 5,
) -> dict:
    if server_kind == "local":
        from labs.lab_04.src.mcp_client_adapter import build_source_server

        server = build_source_server()
        wanted, values = "search_sources", {"query": "Python LLM API evidence", "limit": 3}
        mode = "local"
    else:
        from labs.lab_04.src.job_board_server import build_job_board_server

        server = build_job_board_server(offline=offline)
        wanted, values = "list_openings", {"company": company, "ats": ats, "limit": limit}
        mode = "offline" if offline else "live"

    async with connect(server) as client:
        listing = await client.list_tools()
        result = await call_discovered_tool(client, wanted, values)
        return {
            "mcp_mode": mode,
            "server": client.server_info.name,
            "protocol_version": str(client.protocol_version),
            "declared_tools": [tool.name for tool in listing.tools],
            "called": wanted,
            "records": records_from(result),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Lab 4 MCP client.")
    parser.add_argument("--server", choices=["local", "jobs"], default="local")
    parser.add_argument("--company", default="stripe", help="ATS board slug, e.g. stripe.")
    parser.add_argument("--ats", choices=["greenhouse", "lever", "ashby"], default="greenhouse")
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum short job records to request (default: 5).",
    )
    parser.add_argument("--offline", action="store_true", help="Use the bundled fixture board.")
    args = parser.parse_args()

    payload = anyio.run(run, args.server, args.company, args.ats, args.offline, args.limit)

    print(f"mcp_mode={payload['mcp_mode']}")
    print(f"server={payload['server']}")
    print(f"protocol_version={payload['protocol_version']}")
    print(f"declared_tools={','.join(payload['declared_tools'])}")
    print(f"called={payload['called']}")
    print(f"records={len(payload['records'])}")
    for record in payload["records"]:
        line = record.get("title") or record.get("snippet") or json.dumps(record)
        location = record.get("location")
        print(f"  - {line[:70]}" + (f"  [{location}]" if location else ""))


if __name__ == "__main__":
    main()
