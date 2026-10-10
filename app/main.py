from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import api_router
from app.config import Config
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.core.metrics import metrics_response, record_request
from app.core.rate_limit import InMemoryRateLimiter
from app.schemas import ErrorDetail, ErrorEnvelope, HealthResponse, RootResponse
from app.services import (
    check_mongodb_health,
    check_redis_health,
    get_llm_status,
    get_runtime_status,
    shutdown_background_services,
    startup_background_services,
)


configure_logging()
logger = get_logger(__name__)
rate_limiter = InMemoryRateLimiter()
LOGIN_PATH = "/api/auth/login"


def build_error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str | None,
    details=None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    envelope = ErrorEnvelope(
        error=ErrorDetail(
            code=code,
            message=message,
            details=details,
            request_id=request_id,
            timestamp=datetime.now(timezone.utc),
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json"),
        headers=headers,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    Config.validate_for_boot()
    logger.info(
        "Application configuration loaded",
        extra={"event": "app_config_loaded"},
    )
    await startup_background_services()
    yield
    await shutdown_background_services()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Perfect Engine - Node API System",
        version="2.2.0",
        description="Dynamic AI Bot Engine with multi-runtime orchestration and typed contracts",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=Config.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        request.state.request_id = request_id
        start_time = time.perf_counter()

        if Config.RATE_LIMIT_ENABLED and request.url.path.startswith("/api"):
            client_host = request.client.host if request.client else "unknown"
            if request.url.path == LOGIN_PATH:
                limit, window = Config.LOGIN_RATE_LIMIT_ATTEMPTS, Config.LOGIN_RATE_LIMIT_WINDOW_SECONDS
            else:
                limit, window = Config.RATE_LIMIT_REQUESTS, Config.RATE_LIMIT_WINDOW_SECONDS
            allowed = await rate_limiter.allow(
                f"{client_host}:{request.url.path}",
                limit,
                window,
            )
            if not allowed:
                return build_error_response(
                    status_code=429,
                    code="rate_limit_exceeded",
                    message="Rate limit exceeded",
                    request_id=request_id,
                )

        logger.info(
            "Request started",
            extra={
                "event": "request_started",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
            },
        )

        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        response.headers["x-request-id"] = request_id
        record_request(
            request.method, request.url.path, response.status_code, duration_ms
        )

        logger.info(
            "Request completed",
            extra={
                "event": "request_completed",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        request_id = getattr(request.state, "request_id", None)
        logger.warning(
            exc.message,
            extra={
                "event": "app_error",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": exc.status_code,
                "error_code": exc.code,
            },
        )
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return build_error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
            request_id=request_id,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", None)
        logger.warning(
            "Request validation failed",
            extra={
                "event": "validation_error",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": 422,
                "error_code": "validation_error",
            },
        )
        return build_error_response(
            status_code=422,
            code="validation_error",
            message="Request validation failed",
            details=exc.errors(),
            request_id=request_id,
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        request_id = getattr(request.state, "request_id", None)
        message = str(exc.detail)
        logger.warning(
            message,
            extra={
                "event": "http_error",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": exc.status_code,
                "error_code": "http_error",
            },
        )
        return build_error_response(
            status_code=exc.status_code,
            code="http_error",
            message=message,
            request_id=request_id,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", None)
        logger.exception(
            "Unhandled server error",
            extra={
                "event": "unhandled_exception",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": 500,
                "error_code": "internal_server_error",
            },
        )
        return build_error_response(
            status_code=500,
            code="internal_server_error",
            message="An unexpected error occurred",
            request_id=request_id,
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        request_id = getattr(request.state, "request_id", None)
        return build_error_response(
            status_code=400, code="bad_request", message=str(exc), request_id=request_id
        )

    @app.exception_handler(PermissionError)
    async def permission_error_handler(request: Request, exc: PermissionError):
        request_id = getattr(request.state, "request_id", None)
        return build_error_response(
            status_code=403, code="forbidden", message=str(exc), request_id=request_id
        )

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health_check() -> HealthResponse:
        mongodb = await check_mongodb_health()
        redis = await check_redis_health()
        llm_status = get_llm_status()
        if mongodb != "connected":
            status = "unhealthy"
        elif Config.is_production() and redis not in {"connected"}:
            status = "degraded"
        elif llm_status.startswith("degraded"):
            status = "degraded"
        else:
            status = "healthy"
        return HealthResponse(
            status=status,
            mongodb=mongodb,
            redis=redis,
            llm_provider=Config.LLM_PROVIDER,
            llm_status=llm_status,
            environment=Config.ENVIRONMENT,
            release_version=Config.RELEASE_VERSION,
            auth_enabled=Config.AUTH_ENABLED,
            runtimes=get_runtime_status(),
            timestamp=datetime.now(timezone.utc),
        )

    @app.get("/metrics", tags=["system"])
    async def metrics_endpoint():
        return metrics_response()

    @app.get("/", response_model=RootResponse, tags=["system"])
    async def root() -> RootResponse:
        return RootResponse(
            message="Perfect Engine - Node API System",
            version=Config.RELEASE_VERSION,
            docs="/docs",
            health="/health",
        )

    app.include_router(api_router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=Config.HOST, port=Config.PORT)
