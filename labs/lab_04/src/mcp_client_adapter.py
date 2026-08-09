"""Course-provided MCP boundary: a real server and a real client.

Lab 3 called `search_sources()` as an ordinary Python function. Nothing about
that call declared what the tool takes, what it returns, or who is allowed to
invoke it — the caller simply had the function in scope.

This module puts the *same* function behind an MCP server and reaches it with an
MCP client. The capability does not change; the boundary does. Everything you
see in the trace is produced by the protocol itself:

- `initialize`  negotiates a protocol version and the server's capabilities
- `tools/list`  returns the tool name plus a JSON Schema the server generated
- `resources/list` and `prompts/list` expose the other declared capabilities
- `tools/call`  carries arguments across the boundary and returns content back

The client and server are connected in-process, so no subprocess is spawned and
the labs stay runnable on macOS and Windows alike. The transport is the only
thing that is simplified: the messages, the schema, and the handshake are real.
"""

from __future__ import annotations

import argparse
from time import perf_counter
from typing import Any

import anyio
from mcp import Client
from mcp.server.mcpserver import MCPServer

from labs.lab_04.src.retrieval import retrieve_sources


SERVER_NAME = "job_prep_sources"
SERVER_VERSION = "0.1.0"


class MCPBoundaryError(RuntimeError):
    """Identify the MCP protocol operation that failed."""

    def __init__(
        self,
        operation: str,
        cause: Exception,
        completed_operations: list[dict[str, Any]] | None = None,
        component: str = "labs.lab_04.src.mcp_client_adapter.Client",
    ) -> None:
        super().__init__(f"MCP {operation} failed: {cause}")
        self.operation = operation
        self.completed_operations = list(completed_operations or [])
        self.component = component


def _duration_ms(started: float) -> int:
    return max(1, round((perf_counter() - started) * 1000))


def build_source_server(source_items: list[dict] | None = None) -> MCPServer:
    """An MCP server that publishes the Lab 3 source capability.

    The tool body delegates to the Lab 3 `search_sources()` contract. Lab 4 does
    not implement a second retrieval system, and exposing a capability over MCP
    does not require reimplementing it.
    """
    server = MCPServer(name=SERVER_NAME, version=SERVER_VERSION)

    @server.tool(
        name="search_sources",
        description=(
            "Search the job-material sources for evidence about the candidate, "
            "the role, or the company. Returns records with source_id, title, "
            "path, and the exact snippet that may support a claim."
        ),
    )
    def search_sources(query: str, limit: int = 3) -> list[dict]:
        sources = retrieve_sources(query, limit=limit, source_items=source_items)
        return [source.model_dump() for source in sources]

    @server.resource("job://candidate_profile")
    def candidate_profile() -> str:
        return "The candidate profile supplied for this run."

    @server.resource("job://job_description")
    def job_description() -> str:
        return "The job description supplied for this run."

    @server.prompt(name="job_prep_report")
    def job_prep_report(role: str = "the role") -> str:
        return f"Draft an evidence-backed job-prep report for {role}."

    return server


async def _inspect(server: MCPServer, query: str, limit: int) -> dict[str, Any]:
    protocol_operations: list[dict[str, Any]] = []
    operation = "initialize"
    try:
        client_context = Client(server)
        started = perf_counter()
        async with client_context as client:
            capabilities = "/".join(
                name
                for name, declared in (
                    ("tools", client.server_capabilities.tools),
                    ("resources", client.server_capabilities.resources),
                    ("prompts", client.server_capabilities.prompts),
                )
                if declared is not None
            )
            protocol_operations.append(
                {
                    "operation": operation,
                    "duration_ms": _duration_ms(started),
                    "summary": (
                        f"Negotiated MCP protocol {client.protocol_version} with "
                        f"{client.server_info.name}"
                    ),
                    "details": {
                        "semantic_key": "capability.mcp.initialize",
                        "protocol_version": str(client.protocol_version),
                        "capabilities": capabilities,
                    },
                }
            )

            operation = "tools/list"
            started = perf_counter()
            tools = await client.list_tools()
            search_tool = next(tool for tool in tools.tools if tool.name == "search_sources")
            protocol_operations.append(
                {
                    "operation": operation,
                    "duration_ms": _duration_ms(started),
                    "summary": f"Discovered {len(tools.tools)} MCP tool declaration(s)",
                    "details": {
                        "semantic_key": "capability.mcp.tools.list",
                        "tools": [tool.name for tool in tools.tools],
                        "tool_input_schemas": {
                            tool.name: tool.input_schema for tool in tools.tools
                        },
                    },
                }
            )

            operation = "resources/list"
            started = perf_counter()
            resources = await client.list_resources()
            protocol_operations.append(
                {
                    "operation": operation,
                    "duration_ms": _duration_ms(started),
                    "summary": f"Discovered {len(resources.resources)} MCP resource declaration(s)",
                    "details": {
                        "semantic_key": "capability.mcp.resources.list",
                        "resources": [str(resource.uri) for resource in resources.resources],
                    },
                }
            )

            operation = "prompts/list"
            started = perf_counter()
            prompts = await client.list_prompts()
            protocol_operations.append(
                {
                    "operation": operation,
                    "duration_ms": _duration_ms(started),
                    "summary": f"Discovered {len(prompts.prompts)} MCP prompt declaration(s)",
                    "details": {
                        "semantic_key": "capability.mcp.prompts.list",
                        "prompts": [prompt.name for prompt in prompts.prompts],
                    },
                }
            )

            operation = "tools/call"
            started = perf_counter()
            result = await client.call_tool("search_sources", {"query": query, "limit": limit})
            call_duration_ms = _duration_ms(started)

            # A tool that raises does not raise here: MCP turns it into an error
            # result carried back over the boundary. Before Lab 3 `search_sources()`
            # is implemented, this is the branch you will see.
            structured = {} if result.is_error else (result.structured_content or {})
            returned_source_ids = [
                record["source_id"] for record in structured.get("result", [])
            ]
            protocol_operations.append(
                {
                    "operation": operation,
                    "duration_ms": call_duration_ms,
                    "summary": (
                        "Called search_sources over MCP"
                        + (" and received an error result" if result.is_error else "")
                    ),
                    "details": {
                        "semantic_key": "capability.mcp.tools.call",
                        "tool": "search_sources",
                        "arguments": {"query": query, "limit": limit},
                        "tool_error": bool(result.is_error),
                        "returned_source_ids": returned_source_ids,
                    },
                }
            )
            operation = "close"
            return {
                "status": "ok",
                "server": client.server_info.name,
                "server_version": client.server_info.version,
                "protocol_version": str(client.protocol_version),
                "capabilities": capabilities,
                "tools": [tool.name for tool in tools.tools],
                "tool_count": str(len(tools.tools)),
                "resources": [str(resource.uri) for resource in resources.resources],
                "prompts": [prompt.name for prompt in prompts.prompts],
                "tool_input_schema": search_tool.input_schema,
                "tool_error": bool(result.is_error),
                "returned_source_ids": returned_source_ids,
                "protocol_operations": protocol_operations,
            }
    except MCPBoundaryError as exc:
        if not exc.completed_operations:
            exc.completed_operations = list(protocol_operations)
        raise
    except Exception as exc:
        raise MCPBoundaryError(
            operation,
            exc,
            completed_operations=protocol_operations,
        ) from exc


def inspect_capability_boundary(
    query: str = "Python LLM API evidence",
    limit: int = 3,
    source_items: list[dict] | None = None,
) -> dict[str, Any]:
    """Open a real MCP session and report what the protocol actually declared."""
    return anyio.run(_inspect, build_source_server(source_items), query, limit)


def ping_mcp_example(
    query: str = "Python LLM API evidence",
    limit: int = 3,
    source_items: list[dict] | None = None,
) -> dict[str, Any]:
    return inspect_capability_boundary(query=query, limit=limit, source_items=source_items)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ping", action="store_true")
    args = parser.parse_args()

    if not args.ping:
        raise SystemExit("Use --ping for the Lab 4 MCP example.")

    result = ping_mcp_example()
    print("mcp_ping=ok")
    print(f"server={result['server']} v{result['server_version']}")
    print(f"protocol_version={result['protocol_version']}")
    print(f"capabilities={result['capabilities']}")
    print(f"tools={','.join(result['tools'])}")
    print(f"tool_input_schema={result['tool_input_schema']}")
    if result["tool_error"]:
        print("returned_source_ids=<tools/call returned an error result>")
        print("Finish Lab 3 search_sources() to see records cross the boundary.")
    else:
        print(f"returned_source_ids={','.join(result['returned_source_ids'])}")


if __name__ == "__main__":
    main()
