from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


class AppError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int,
        details: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details
        self.timestamp = datetime.now(timezone.utc)


class NotFoundError(AppError):
    def __init__(
        self, message: str, *, code: str = "not_found", details: Optional[Any] = None
    ) -> None:
        super().__init__(message, code=code, status_code=404, details=details)


class BadRequestError(AppError):
    def __init__(
        self, message: str, *, code: str = "bad_request", details: Optional[Any] = None
    ) -> None:
        super().__init__(message, code=code, status_code=400, details=details)


class InternalServerError(AppError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "internal_error",
        details: Optional[Any] = None,
    ) -> None:
        super().__init__(message, code=code, status_code=500, details=details)
