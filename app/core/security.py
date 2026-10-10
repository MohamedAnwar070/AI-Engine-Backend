from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import Config
from app.core.errors import InternalServerError


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return pwd_context.verify(password, hashed_password)


def create_access_token(
    subject: str, role: str, expires_delta: Optional[timedelta] = None
) -> str:
    if not Config.JWT_SECRET_KEY:
        raise InternalServerError(
            "JWT secret is not configured", code="jwt_secret_missing"
        )

    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=Config.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload: Dict[str, Any] = {"sub": subject, "role": role, "exp": expire}
    return jwt.encode(payload, Config.JWT_SECRET_KEY, algorithm=Config.JWT_ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    if not Config.JWT_SECRET_KEY:
        raise InternalServerError(
            "JWT secret is not configured", code="jwt_secret_missing"
        )
    try:
        return jwt.decode(
            token, Config.JWT_SECRET_KEY, algorithms=[Config.JWT_ALGORITHM]
        )
    except JWTError as exc:
        raise ValueError("Invalid access token") from exc
