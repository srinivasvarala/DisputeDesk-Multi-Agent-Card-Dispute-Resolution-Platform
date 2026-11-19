"""Typed tool parameters shared by the MCP servers.

Tools take flat, typed arguments built from these aliases. The SDK turns each tool signature
into a Pydantic model and validates every call against it before the tool body runs.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from pydantic import Field

CustomerIdParam = Annotated[
    str, Field(pattern=r"^cus_\d{4}$", description="Customer ID, e.g. cus_0001")
]
MerchantIdParam = Annotated[
    str, Field(pattern=r"^mer_\d{3}$", description="Merchant ID, e.g. mer_001")
]
TxnIdParam = Annotated[
    str, Field(pattern=r"^txn_\d{6}$", description="Transaction ID, e.g. txn_000001")
]
CaseIdParam = Annotated[str, Field(pattern=r"^case_\d{4}$", description="Case ID, e.g. case_0001")]
AgentNameParam = Annotated[
    str,
    Field(
        pattern=r"^[a-z][a-z0-9_-]{1,39}$",
        description="Name of the agent or user writing this, e.g. resolution-agent",
    ),
]

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)
