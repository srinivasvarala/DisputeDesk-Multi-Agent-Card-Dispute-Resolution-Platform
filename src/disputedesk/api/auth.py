"""FastAPI auth dependencies: who is calling (JWT) and may they do this (scope).

401 = no valid token. 403 = valid token, missing scope. Both are reported to `on_denied` so the
app can write them to the audit log.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from disputedesk.config import Settings
from disputedesk.security.jwt import AuthError, Principal, verify_token
from disputedesk.security.scopes import Scope

# (principal or None, scope wanted, request path, status code)
DeniedHook = Callable[[Principal | None, Scope, str, int], None]

_bearer = HTTPBearer(auto_error=False)


class Authenticator:
    def __init__(self, settings: Settings, on_denied: DeniedHook | None = None) -> None:
        self._settings = settings
        self._on_denied = on_denied

    def require(self, scope: Scope) -> Callable[..., Principal]:
        """A dependency that returns the caller when their role grants `scope`."""

        def dependency(
            request: Request,
            creds: HTTPAuthorizationCredentials | None = Depends(_bearer),  # noqa: B008
        ) -> Principal:
            principal: Principal | None = None
            if creds is not None:
                try:
                    principal = verify_token(self._settings, creds.credentials)
                except AuthError:
                    principal = None
            if principal is None:
                self._deny(None, scope, request, status.HTTP_401_UNAUTHORIZED)
            elif scope not in principal.scopes:
                self._deny(principal, scope, request, status.HTTP_403_FORBIDDEN)
            assert principal is not None  # _deny always raises
            return principal

        return dependency

    def _deny(self, principal: Principal | None, scope: Scope, request: Request, code: int) -> None:
        if self._on_denied is not None:
            self._on_denied(principal, scope, request.url.path, code)
        if code == status.HTTP_401_UNAUTHORIZED:
            raise HTTPException(
                code, "authentication required", headers={"WWW-Authenticate": "Bearer"}
            )
        raise HTTPException(code, "insufficient scope")
