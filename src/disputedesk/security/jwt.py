"""JWT issue and verify (HS256, shared secret from the environment).

Verification pins the algorithm, so `alg=none` and algorithm-confusion tokens are rejected, and
requires `exp`, `iss`, `aud` and `sub`. The role claim is mapped to scopes by `scopes.py`; scopes
inside the token are never trusted. Cognito replaces this in M9 behind the same two functions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from disputedesk.config import Settings
from disputedesk.security.scopes import Role, Scope, scopes_for

ALGORITHM = "HS256"
MIN_SECRET_CHARS = 32


class AuthError(Exception):
    """The token is missing, malformed, expired or not signed by us."""


class Principal(BaseModel):
    """The authenticated caller, as far as the API is concerned."""

    model_config = ConfigDict(frozen=True)

    sub: str = Field(min_length=1, max_length=100)
    role: Role

    @property
    def scopes(self) -> frozenset[Scope]:
        return scopes_for(self.role)


def _secret(settings: Settings) -> str:
    if settings.jwt_secret is None:
        raise RuntimeError("JWT_SECRET is not set")
    secret = settings.jwt_secret.get_secret_value()
    if len(secret) < MIN_SECRET_CHARS:
        raise RuntimeError(f"JWT_SECRET must be at least {MIN_SECRET_CHARS} characters")
    return secret


def require_jwt_config(settings: Settings) -> None:
    """Fail at startup, not on the first request, when the secret is missing or weak."""
    _secret(settings)


def issue_token(settings: Settings, *, sub: str, role: Role, now: datetime | None = None) -> str:
    issued = now or datetime.now(UTC)
    claims = {
        "sub": sub,
        "role": role.value,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": issued,
        "exp": issued + timedelta(minutes=settings.jwt_ttl_minutes),
    }
    return jwt.encode(claims, _secret(settings), algorithm=ALGORITHM)


def verify_token(settings: Settings, token: str) -> Principal:
    try:
        claims = jwt.decode(
            token,
            _secret(settings),
            algorithms=[ALGORITHM],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
        return Principal(sub=claims["sub"], role=Role(claims["role"]))
    except (jwt.PyJWTError, ValidationError, KeyError, ValueError):
        # One generic error: callers learn nothing about why a token was refused.
        raise AuthError("invalid token") from None
