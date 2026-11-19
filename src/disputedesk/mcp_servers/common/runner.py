"""Shared entry point so each server runs standalone: `python -m disputedesk.mcp_servers.<name>`."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from mcp.server.mcpserver import MCPServer

from disputedesk.db import apply_schema, connect
from disputedesk.mcp_servers.common.store import PostgresStore


def run_server(name: str, build: Callable[[PostgresStore], MCPServer], default_port: int) -> None:
    parser = argparse.ArgumentParser(description=f"DisputeDesk {name} MCP server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="streamable-http only")
    parser.add_argument("--port", type=int, default=default_port, help="streamable-http only")
    args = parser.parse_args()

    with connect() as conn:
        apply_schema(conn)
        server = build(PostgresStore(conn))
        if args.transport == "stdio":
            server.run("stdio")
        else:
            server.run("streamable-http", host=args.host, port=args.port)
