"""Single-admin JWT auth, permission model, and route guards (no network, no DB)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from app.auth import Principal, get_current_principal, require_permission
from app.config import Config
from app.core import permissions
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.security import (
    TOKEN_ISSUER,
    TokenInvalidError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

SECRET = "x" * 40


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(Config, "AUTH_ENABLED", True)
    monkeypatch.setattr(Config, "JWT_SECRET_KEY", SECRET)
    monkeypatch.setattr(Config, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(Config, "ACCESS_TOKEN_EXPIRE_MINUTES", 60)
    monkeypatch.setattr(Config, "ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setattr(Config, "ADMIN_PASSWORD_HASH", hash_password("correct-horse-battery"))


def test_password_hash_roundtrip():
    stored = hash_password("correct-horse-battery")
    assert verify_password("correct-horse-battery", stored)
    assert not verify_password("wrong-password-value", stored)
    assert not verify_password("anything", "")


def test_access_token_carries_role_and_expiry(auth_on):
    token = create_access_token(subject="admin", role="admin")
    claims = decode_access_token(token)
    assert claims["sub"] == "admin"
    assert claims["role"] == "admin"
    assert claims["typ"] == "access"
    assert claims["iss"] == TOKEN_ISSUER
    assert claims["exp"] - claims["iat"] == 60 * 60


def test_expired_token_is_rejected(auth_on):
    token = create_access_token(subject="admin", role="admin", expires_delta=timedelta(seconds=-5))
    with pytest.raises(TokenInvalidError):
        decode_access_token(token)


def test_forged_signature_is_rejected(auth_on):
    forged = jwt.encode(
        {"sub": "admin", "role": "admin", "typ": "access", "iss": TOKEN_ISSUER},
        "y" * 40,
        algorithm="HS256",
    )
    with pytest.raises(TokenInvalidError):
        decode_access_token(forged)


def test_refresh_style_token_type_is_rejected(auth_on):
    token = jwt.encode(
        {"sub": "admin", "role": "admin", "typ": "refresh", "iss": TOKEN_ISSUER},
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(TokenInvalidError):
        decode_access_token(token)


def test_unknown_permission_fails_at_definition_time():
    with pytest.raises(ValueError):
        require_permission("projects:nope")


def test_admin_holds_every_permission():
    assert permissions.effective_permissions("admin") == permissions.PERMISSIONS
    for name in permissions.PERMISSIONS:
        assert permissions.role_has_permission("admin", name)


def test_unknown_role_has_no_permissions():
    assert permissions.effective_permissions("ghost") == frozenset()
    assert not permissions.role_has_permission("ghost", "projects:read")


@pytest.mark.asyncio
async def test_missing_credentials_raise_401_when_auth_on(auth_on):
    with pytest.raises(AuthenticationError) as exc:
        await get_current_principal(None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_valid_admin_token_resolves_principal(auth_on):
    from fastapi.security import HTTPAuthorizationCredentials

    token = create_access_token(subject="admin", role="admin")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    principal = await get_current_principal(creds)
    assert principal.subject == "admin"
    assert principal.role == "admin"
    assert principal.email == "admin@example.com"


def _app_with_guard(permission: str) -> FastAPI:
    from fastapi import Depends

    from app.main import app as real_app  # noqa: F401  (ensures handlers are wired)

    app = FastAPI()

    @app.exception_handler(AuthenticationError)
    async def _401(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=401, content={"error": {"code": exc.code}})

    @app.exception_handler(PermissionDeniedError)
    async def _403(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=403, content={"error": {"code": exc.code}})

    @app.get("/guarded")
    async def guarded(_: Principal = Depends(require_permission(permission))):
        return {"ok": True}

    return app


def test_route_without_token_returns_401(auth_on):
    client = TestClient(_app_with_guard("projects:read"))
    assert client.get("/guarded").status_code == 401


def test_route_with_admin_token_returns_200(auth_on):
    client = TestClient(_app_with_guard("projects:read"))
    token = create_access_token(subject="admin", role="admin")
    response = client.get("/guarded", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


def test_role_without_permission_returns_403(auth_on, monkeypatch):
    monkeypatch.setitem(permissions.ROLE_PERMISSIONS, "monitor", frozenset({"stats:read"}))
    client = TestClient(_app_with_guard("projects:write"))
    token = create_access_token(subject="admin", role="monitor")
    response = client.get("/guarded", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_role_with_permission_returns_200(auth_on, monkeypatch):
    monkeypatch.setitem(permissions.ROLE_PERMISSIONS, "monitor", frozenset({"stats:read"}))
    client = TestClient(_app_with_guard("stats:read"))
    token = create_access_token(subject="admin", role="monitor")
    response = client.get("/guarded", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


def test_auth_disabled_allows_local_development(monkeypatch):
    monkeypatch.setattr(Config, "AUTH_ENABLED", False)
    client = TestClient(_app_with_guard("projects:write"))
    assert client.get("/guarded").status_code == 200
