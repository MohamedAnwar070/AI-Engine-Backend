from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import Config
from app.core.errors import InternalServerError


ACCESS_TOKEN_TYPE = "access"
TOKEN_ISSUER = "perfect-engine"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    if not hashed_password:
        return False
    try:
        return pwd_context.verify(password, hashed_password)
    except ValueError:
        return False


def create_access_token(
    subject: str,
    role: str,
    expires_delta: Optional[timedelta] = None,
) -> str:
    if not Config.JWT_SECRET_KEY:
        raise InternalServerError(
            "JWT secret is not configured", code="jwt_secret_missing"
        )
    now = datetime.now(timezone.utc)
    expire = now + (
        expires_delta or timedelta(minutes=Config.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload: Dict[str, Any] = {
        "sub": subject,
        "role": role,
        "typ": ACCESS_TOKEN_TYPE,
        "iss": TOKEN_ISSUER,
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(payload, Config.JWT_SECRET_KEY, algorithm=Config.JWT_ALGORITHM)


class TokenInvalidError(Exception):
    """Raised when a bearer token is missing, malformed, expired, or forged."""


def decode_access_token(token: str) -> Dict[str, Any]:
    if not Config.JWT_SECRET_KEY:
        raise InternalServerError(
            "JWT secret is not configured", code="jwt_secret_missing"
        )
    try:
        claims = jwt.decode(
            token,
            Config.JWT_SECRET_KEY,
            algorithms=[Config.JWT_ALGORITHM],
            issuer=TOKEN_ISSUER,
        )
    except JWTError as exc:
        raise TokenInvalidError("Invalid or expired access token") from exc
    if claims.get("typ") != ACCESS_TOKEN_TYPE or not claims.get("sub"):
        raise TokenInvalidError("Invalid access token")
    return claims
