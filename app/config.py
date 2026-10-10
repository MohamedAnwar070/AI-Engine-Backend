"""
Configuration Module
====================

Centralized configuration for Perfect Engine
"""

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Config:
    """Application configuration"""

    # MongoDB Configuration
    MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    DATABASE_NAME = os.getenv("DATABASE_NAME", "arffy_AIbot_engine")

    # LLM Configuration
    # LiteLLM cloud gateway only (no local models). Providers live in MongoDB
    # (manage via /api/llm-providers) and swap by config — no redeploy.
    #   litellm -> DB collection llm_providers + env fallback + keyword fallback
    #   openai  -> legacy single-provider via OPENAI_API_KEY
    #   keyword -> zero-dependency lexical router (offline dev / fallback)
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "litellm")

    # LiteLLM runtime tuning
    LITELLM_TIMEOUT_SECONDS = float(os.getenv("LITELLM_TIMEOUT_SECONDS", "60"))
    LITELLM_MAX_RETRIES = int(os.getenv("LITELLM_MAX_RETRIES", "0"))
    LITELLM_NUM_FALLBACKS = int(os.getenv("LITELLM_NUM_FALLBACKS", "3"))
    LITELLM_CACHE_TTL_SECONDS = int(os.getenv("LITELLM_CACHE_TTL_SECONDS", "10"))

    # Secret encryption for DB-stored provider API keys. Any string works
    # (derived to a Fernet key via SHA-256); falls back to JWT secret.
    LLM_KEY_ENCRYPTION_KEY = os.getenv(
        "LLM_KEY_ENCRYPTION_KEY", ""
    ) or os.getenv("JWT_SECRET_KEY", "")

    # Cloud provider keys (bootstrap / fallback when DB has no entry).
    # Preferred path is DB storage via POST /api/llm-providers; env keys are
    # used for seeding and as fallback so containers stay 12-factor clean.
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
    MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")

    # OpenAI Configuration (legacy single-provider + env seed for litellm)
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

    # Server Configuration
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", "8000"))
    ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
    RELEASE_VERSION = os.getenv("RELEASE_VERSION", "local")
    AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
    JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    # Single admin account. The password is never stored: ADMIN_PASSWORD_HASH
    # is a bcrypt hash produced by scripts/make_admin_hash.py.
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
    ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")
    RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"
    RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "120"))
    RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
    LOGIN_RATE_LIMIT_ATTEMPTS = int(os.getenv("LOGIN_RATE_LIMIT_ATTEMPTS", "5"))
    LOGIN_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "300"))
    ALERT_LATENCY_MS_THRESHOLD = float(os.getenv("ALERT_LATENCY_MS_THRESHOLD", "2500"))
    ALERT_ERROR_RATE_THRESHOLD = float(os.getenv("ALERT_ERROR_RATE_THRESHOLD", "15"))

    # Execution Runtime Configuration
    NODE_RUNTIME_POOL_SIZE = int(os.getenv("NODE_RUNTIME_POOL_SIZE", "2"))
    NODE_EXECUTION_TIMEOUT_MS = int(os.getenv("NODE_EXECUTION_TIMEOUT_MS", "30000"))
    NODE_EXECUTION_CACHE_TTL_SECONDS = int(
        os.getenv("NODE_EXECUTION_CACHE_TTL_SECONDS", "120")
    )
    NODE_EXECUTION_MAX_RETRIES = int(os.getenv("NODE_EXECUTION_MAX_RETRIES", "1"))
    REDIS_URL = os.getenv("REDIS_URL", "")

    # Async Test Queue Configuration
    TEST_QUEUE_ASYNC_THRESHOLD = int(os.getenv("TEST_QUEUE_ASYNC_THRESHOLD", "5"))
    TEST_QUEUE_WORKERS = int(os.getenv("TEST_QUEUE_WORKERS", "1"))

    # Multi-runtime configuration
    PYTHON_RUNTIME_POOL_SIZE = int(os.getenv("PYTHON_RUNTIME_POOL_SIZE", "2"))
    RUNTIME_IDLE_TTL_SECONDS = int(os.getenv("RUNTIME_IDLE_TTL_SECONDS", "300"))
    CONVERSATION_MESSAGE_LIMIT = int(os.getenv("CONVERSATION_MESSAGE_LIMIT", "60"))

    # CORS Configuration
    ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

    @classmethod
    def is_production(cls) -> bool:
        return cls.ENVIRONMENT.lower() in {"production", "prod", "staging"}

    @classmethod
    def validate_for_boot(cls) -> None:
        """Fail closed on misconfigured auth, and on unsafe production settings."""
        errors = []
        if cls.AUTH_ENABLED or cls.is_production():
            if not cls.JWT_SECRET_KEY or len(cls.JWT_SECRET_KEY) < 32:
                errors.append(
                    "JWT_SECRET_KEY must be set (min 32 chars) when auth is enabled or in production"
                )
        if cls.AUTH_ENABLED:
            if not cls.ADMIN_EMAIL or "@" not in cls.ADMIN_EMAIL:
                errors.append("ADMIN_EMAIL must be set when AUTH_ENABLED=true")
            if not cls.ADMIN_PASSWORD_HASH.startswith("$2"):
                errors.append(
                    "ADMIN_PASSWORD_HASH must be a bcrypt hash when AUTH_ENABLED=true"
                )
            if cls.ACCESS_TOKEN_EXPIRE_MINUTES < 1:
                errors.append("ACCESS_TOKEN_EXPIRE_MINUTES must be at least 1")
        if cls.is_production():
            if not cls.AUTH_ENABLED:
                errors.append("AUTH_ENABLED must be true in production/staging")
            if not cls.REDIS_URL:
                errors.append("REDIS_URL is required in production/staging")
            if "*" in cls.ALLOWED_ORIGINS and len(cls.ALLOWED_ORIGINS) == 1:
                errors.append(
                    "ALLOWED_ORIGINS must not be '*' alone in production/staging"
                )
        if errors:
            raise RuntimeError("Configuration invalid:\n- " + "\n- ".join(errors))

    @classmethod
    def display_config(cls):
        """Display current configuration"""
        print("Configuration loaded:")
        print(f"   MongoDB: {cls.MONGODB_URL}")
        print(f"   Database: {cls.DATABASE_NAME}")
        print(f"   LLM Provider: {cls.LLM_PROVIDER}")
        print(f"   Environment: {cls.ENVIRONMENT}")
        print(f"   Auth enabled: {cls.AUTH_ENABLED}")
        print(f"   Host: {cls.HOST}:{cls.PORT}")
