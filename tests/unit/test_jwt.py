from datetime import UTC, datetime, timedelta

import jwt
import pytest

from disputedesk.config import Settings
from disputedesk.security.jwt import (
    AuthError,
    issue_token,
    require_jwt_config,
    verify_token,
)
from disputedesk.security.scopes import Role, Scope, scopes_for
from tests.conftest import make_settings

SECRET = "x" * 40


@pytest.fixture
def settings() -> Settings:
    return make_settings(jwt_secret=SECRET)


def test_roundtrip_gives_role_and_scopes(settings: Settings) -> None:
    token = issue_token(settings, sub="ana", role=Role.APPROVER)
    principal = verify_token(settings, token)
    assert principal.sub == "ana"
    assert Scope.DISPUTES_APPROVE in principal.scopes


def test_role_scopes_are_least_privilege() -> None:
    assert Scope.DISPUTES_APPROVE not in scopes_for(Role.ANALYST)
    assert Scope.AUDIT_READ not in scopes_for(Role.APPROVER)
    assert Scope.AUDIT_READ in scopes_for(Role.ADMIN)


def test_expired_token_is_rejected(settings: Settings) -> None:
    old = datetime.now(UTC) - timedelta(hours=3)
    with pytest.raises(AuthError):
        verify_token(settings, issue_token(settings, sub="a", role=Role.ADMIN, now=old))


def test_wrong_signature_is_rejected(settings: Settings) -> None:
    other = make_settings(jwt_secret="y" * 40)
    with pytest.raises(AuthError):
        verify_token(settings, issue_token(other, sub="a", role=Role.ADMIN))


def test_alg_none_is_rejected(settings: Settings) -> None:
    claims = {
        "sub": "a",
        "role": "admin",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    token = jwt.encode(claims, key=None, algorithm="none")  # type: ignore[arg-type]
    with pytest.raises(AuthError):
        verify_token(settings, token)


@pytest.mark.parametrize("claim", ["iss", "aud"])
def test_wrong_issuer_or_audience_is_rejected(settings: Settings, claim: str) -> None:
    other = make_settings(
        jwt_secret=SECRET, **{f"jwt_{'issuer' if claim == 'iss' else 'audience'}": "evil"}
    )
    with pytest.raises(AuthError):
        verify_token(settings, issue_token(other, sub="a", role=Role.ADMIN))


def test_unknown_role_is_rejected(settings: Settings) -> None:
    claims = {
        "sub": "a",
        "role": "root",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    with pytest.raises(AuthError):
        verify_token(settings, jwt.encode(claims, SECRET, algorithm="HS256"))


def test_garbage_is_rejected(settings: Settings) -> None:
    with pytest.raises(AuthError):
        verify_token(settings, "not-a-jwt")


def test_missing_or_short_secret_fails_startup() -> None:
    with pytest.raises(RuntimeError):
        require_jwt_config(make_settings())
    with pytest.raises(RuntimeError):
        require_jwt_config(make_settings(jwt_secret="short"))
