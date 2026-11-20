"""Roles and scopes. Plain Python, no LLM: who may do what is a fixed table."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ANALYST = "analyst"
    APPROVER = "approver"
    ADMIN = "admin"


class Scope(StrEnum):
    DISPUTES_READ = "disputes:read"
    DISPUTES_START = "disputes:start"
    DISPUTES_APPROVE = "disputes:approve"
    AUDIT_READ = "audit:read"


_ANALYST = frozenset({Scope.DISPUTES_READ, Scope.DISPUTES_START})
_APPROVER = _ANALYST | {Scope.DISPUTES_APPROVE}

ROLE_SCOPES: dict[Role, frozenset[Scope]] = {
    Role.ANALYST: _ANALYST,
    Role.APPROVER: _APPROVER,
    Role.ADMIN: _APPROVER | {Scope.AUDIT_READ},
}


def scopes_for(role: Role) -> frozenset[Scope]:
    return ROLE_SCOPES[role]
