"""Tool errors. Each maps to an audit outcome; the message is returned to the MCP client."""

from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError


class InvalidRequestError(ToolError):
    """Arguments are well-typed but invalid together (e.g. since > until, unknown argument)."""


class NotFoundError(ToolError):
    """A referenced entity does not exist."""


class ConflictError(ToolError):
    """The request is valid but not allowed in the entity's current state."""
