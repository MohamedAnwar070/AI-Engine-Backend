"""Roles and permissions for the engine.

Today there is one role, ``admin``, which holds every permission (``*``).
Additional roles are added here: define the role, list its permissions, and
guard routes with ``require_permission("<permission>")``. Routes never check
role names directly, so a new role only changes this file.
"""

from __future__ import annotations

from typing import Dict, FrozenSet

ALL_PERMISSIONS = "*"

# Permission names are "<area>:<action>". Keep this list in sync with the
# route guards; ``require_permission`` rejects any name not listed here at
# import time, so a typo fails fast instead of silently denying access.
PERMISSIONS: FrozenSet[str] = frozenset(
    {
        "auth:me",
        "projects:read",
        "projects:write",
        "projects:delete",
        "projects:import_export",
        "projects:test",
        "nodes:read",
        "nodes:write",
        "nodes:delete",
        "nodes:rollback",
        "providers:read",
        "providers:manage",
        "chat:use",
        "executions:read",
        "executions:run",
        "executions:replay",
        "conversations:read",
        "templates:read",
        "templates:write",
        "presets:read",
        "presets:write",
        "governance:read",
        "monitoring:read",
        "alerts:receive",
        "stats:read",
    }
)

ROLE_PERMISSIONS: Dict[str, FrozenSet[str]] = {
    "admin": frozenset({ALL_PERMISSIONS}),
}

DEFAULT_ROLE = "admin"


def effective_permissions(role: str) -> FrozenSet[str]:
    """Concrete permission names for a role; ``*`` expands to the full list."""
    granted = ROLE_PERMISSIONS.get(role, frozenset())
    if ALL_PERMISSIONS in granted:
        return PERMISSIONS
    return granted


def role_has_permission(role: str, permission: str) -> bool:
    return permission in effective_permissions(role)


def assert_known_permission(permission: str) -> None:
    if permission not in PERMISSIONS:
        raise ValueError(f"Unknown permission: {permission}")
