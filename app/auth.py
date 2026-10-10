from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from bson import ObjectId

from app.config import Config
from app.core.errors import AuthenticationError, BadRequestError, PermissionDeniedError
from app.core.permissions import (
    DEFAULT_ROLE,
    ROLE_PERMISSIONS,
    assert_known_permission,
    effective_permissions,
    role_has_permission,
)
from app.core.security import (
    TokenInvalidError,
    create_access_token,
    decode_access_token,
    verify_password,
)
from app.database import governance_audits_collection
from app.schemas import AuthLoginRequest, TokenResponse, UserResponse


ADMIN_SUBJECT = "admin"
SYSTEM_SUBJECT = "system"

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    subject: str
    email: str
    role: str


def to_user_response(principal: Principal) -> UserResponse:
    return UserResponse(
        user_id=principal.subject,
        email=principal.email,
        role=principal.role,
        permissions=sorted(effective_permissions(principal.role)),
    )


async def write_governance_audit(
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    actor: Optional[Principal] = None,
    details: Optional[dict] = None,
) -> None:
    await governance_audits_collection.insert_one(
        {
            "audit_id": f"gaudit_{ObjectId()}",
            "actor_user_id": actor.subject if actor else None,
            "actor_email": actor.email if actor else None,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "details": details or {},
            "created_at": datetime.now(timezone.utc),
        }
    )


async def login_admin(credentials: AuthLoginRequest) -> TokenResponse:
    if not Config.AUTH_ENABLED:
        raise BadRequestError(
            "Authentication is disabled on this engine", code="auth_disabled"
        )
    email_matches = (
        credentials.email.strip().lower() == Config.ADMIN_EMAIL.strip().lower()
    )
    # Always run the hash check so response timing does not reveal whether
    # the email matched.
    password_ok = verify_password(credentials.password, Config.ADMIN_PASSWORD_HASH)
    if not (email_matches and password_ok):
        raise AuthenticationError(
            "Invalid email or password", code="invalid_credentials"
        )

    principal = Principal(
        subject=ADMIN_SUBJECT,
        email=Config.ADMIN_EMAIL.strip().lower(),
        role=DEFAULT_ROLE,
    )
    token = create_access_token(subject=principal.subject, role=principal.role)
    await write_governance_audit("login", "admin", principal.subject, actor=principal)
    return TokenResponse(
        access_token=token,
        expires_in=Config.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=to_user_response(principal),
    )


def _system_principal() -> Principal:
    return Principal(
        subject=SYSTEM_SUBJECT,
        email="system@local",
        role=DEFAULT_ROLE,
    )


async def get_current_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Principal:
    if not Config.AUTH_ENABLED:
        # Local development without auth. validate_for_boot() refuses this
        # configuration in production and staging.
        return _system_principal()
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Authentication required", code="auth_required")
    try:
        claims = decode_access_token(credentials.credentials)
    except TokenInvalidError as exc:
        raise AuthenticationError(str(exc), code="invalid_token") from exc

    role = claims.get("role")
    if claims.get("sub") != ADMIN_SUBJECT or role not in ROLE_PERMISSIONS:
        raise AuthenticationError("Invalid access token", code="invalid_token")
    return Principal(
        subject=ADMIN_SUBJECT,
        email=Config.ADMIN_EMAIL.strip().lower(),
        role=role,
    )


def require_permission(permission: str):
    """Allow the request only when the caller's role grants ``permission``.

    Unknown permission names raise at import time, so a typo in a route
    cannot silently deny (or open) access.
    """
    assert_known_permission(permission)

    async def dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if not role_has_permission(principal.role, permission):
            raise PermissionDeniedError(
                f"Missing permission: {permission}", code="forbidden"
            )
        return principal

    return dependency
