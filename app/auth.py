from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Config
from app.core.errors import NotFoundError
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.database import governance_audits_collection, users_collection
from app.schemas import AuthLoginRequest, TokenResponse, UserCreate, UserResponse


bearer_scheme = HTTPBearer(auto_error=False)


def _serialize(document):
    if not document:
        return None
    output = dict(document)
    output["_id"] = str(output["_id"])
    output.pop("password_hash", None)
    if isinstance(output.get("created_at"), datetime):
        output["created_at"] = output["created_at"].isoformat()
    return output


async def write_governance_audit(
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    actor: Optional[dict] = None,
    details: Optional[dict] = None,
) -> None:
    await governance_audits_collection.insert_one(
        {
            "audit_id": f"gaudit_{ObjectId()}",
            "actor_user_id": actor.get("user_id") if actor else None,
            "actor_email": actor.get("email") if actor else None,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "details": details or {},
            "created_at": datetime.now(timezone.utc),
        }
    )


async def ensure_default_admin() -> None:
    if not Config.AUTH_ENABLED:
        return
    if not (
        Config.DEFAULT_ADMIN_EMAIL
        and Config.DEFAULT_ADMIN_PASSWORD
        and Config.DEFAULT_ADMIN_NAME
    ):
        return
    existing = await users_collection.find_one(
        {"email": Config.DEFAULT_ADMIN_EMAIL.lower()}
    )
    if existing:
        await users_collection.update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "full_name": Config.DEFAULT_ADMIN_NAME,
                    "password_hash": hash_password(Config.DEFAULT_ADMIN_PASSWORD),
                    "role": "admin",
                    "is_active": True,
                }
            },
        )
        return
    payload = {
        "user_id": f"user_{ObjectId()}",
        "email": Config.DEFAULT_ADMIN_EMAIL.lower(),
        "full_name": Config.DEFAULT_ADMIN_NAME,
        "password_hash": hash_password(Config.DEFAULT_ADMIN_PASSWORD),
        "role": "admin",
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }
    await users_collection.insert_one(payload)


async def register_user(user: UserCreate, actor: Optional[dict] = None) -> UserResponse:
    existing = await users_collection.find_one({"email": user.email.lower()})
    if existing:
        raise ValueError("User already exists")
    payload = {
        "user_id": f"user_{ObjectId()}",
        "email": user.email.lower(),
        "full_name": user.full_name,
        "password_hash": hash_password(user.password),
        "role": user.role,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }
    result = await users_collection.insert_one(payload)
    payload["_id"] = result.inserted_id
    await write_governance_audit(
        "register_user",
        "user",
        payload["user_id"],
        actor=actor,
        details={"role": user.role},
    )
    return UserResponse.model_validate(_serialize(payload))


async def login_user(credentials: AuthLoginRequest) -> TokenResponse:
    user = await users_collection.find_one({"email": credentials.email.lower()})
    if not user or not verify_password(
        credentials.password, user.get("password_hash", "")
    ):
        raise ValueError("Invalid credentials")
    if not user.get("is_active", True):
        raise ValueError("User is inactive")
    token = create_access_token(subject=user["user_id"], role=user["role"])
    await write_governance_audit("login", "user", user["user_id"], actor=user)
    return TokenResponse(
        access_token=token, user=UserResponse.model_validate(_serialize(user))
    )


async def _fetch_user_from_token(
    credentials: Optional[HTTPAuthorizationCredentials],
) -> Optional[dict]:
    if not credentials:
        return None
    payload = decode_access_token(credentials.credentials)
    user = await users_collection.find_one({"user_id": payload.get("sub")})
    return user


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Optional[dict]:
    if not Config.AUTH_ENABLED:
        return None
    if not credentials:
        return None
    user = await _fetch_user_from_token(credentials)
    if not user:
        raise NotFoundError("Authenticated user not found", code="user_not_found")
    return user


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> dict:
    if not Config.AUTH_ENABLED:
        return {
            "_id": "system",
            "user_id": "system",
            "email": "system@local",
            "role": "admin",
            "full_name": "System",
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
        }
    if not credentials:
        raise ValueError("Authentication required")
    user = await _fetch_user_from_token(credentials)
    if not user:
        raise NotFoundError("Authenticated user not found", code="user_not_found")
    return user


def require_roles(*roles: str):
    async def dependency(user: dict = Depends(get_current_user)) -> dict:
        if Config.AUTH_ENABLED and user.get("role") not in roles:
            raise PermissionError("Insufficient permissions")
        return user

    return dependency
