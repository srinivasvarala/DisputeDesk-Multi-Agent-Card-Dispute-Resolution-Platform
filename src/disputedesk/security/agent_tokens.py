"""Short-lived credentials an agent presents to the MCP servers.

The `ToolBox` mints one per tool call, naming the agent and the tools its allowlist grants. The
server verifies the signature and checks the tool itself, so a caller that skips the `ToolBox`,
or that claims another agent's name, cannot reach a tool it was not granted. The audience differs
from user tokens (`disputedesk-api`), so a user token never works here, nor the reverse.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import jwt
from pydantic import BaseModel, ConfigDict, Field

from disputedesk.config import Settings
from disputedesk.security.jwt import ALGORITHM, AuthError, signing_secret

META_KEY = "disputedesk/credential"  # key in the MCP request `_meta`
AUDIENCE = "disputedesk-mcp"
TTL = timedelta(seconds=60)


class AgentIdentity(BaseModel):
    """A verified agent and the tools it was granted."""

    model_config = ConfigDict(frozen=True)

    agent: str = Field(min_length=1, max_length=100)
    tools: frozenset[str]


class NoAuth:
    """Explicit opt-out of credential checks, for the standalone demo servers and unit tests of
    a single server. Servers must be given a verifier or this; there is no silent default."""


NO_AUTH = NoAuth()


class CredentialIssuer:
    def __init__(self, settings: Settings) -> None:
        self._secret = signing_secret(settings)
        self._issuer = settings.jwt_issuer

    def issue(self, agent: str, tools: Iterable[str], *, now: datetime | None = None) -> str:
        issued = now or datetime.now(UTC)
        claims = {
            "sub": agent,
            "tools": sorted(tools),
            "iss": self._issuer,
            "aud": AUDIENCE,
            "iat": issued,
            "exp": issued + TTL,
        }
        return jwt.encode(claims, self._secret, algorithm=ALGORITHM)


class CredentialVerifier:
    def __init__(self, settings: Settings) -> None:
        self._secret = signing_secret(settings)
        self._issuer = settings.jwt_issuer

    def verify(self, token: object) -> AgentIdentity:
        if not isinstance(token, str):
            raise AuthError("missing credential")
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[ALGORITHM],
                issuer=self._issuer,
                audience=AUDIENCE,
                options={"require": ["exp", "iss", "aud", "sub", "tools"]},
            )
            return AgentIdentity(agent=claims["sub"], tools=frozenset(claims["tools"]))
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise AuthError("invalid credential") from None
