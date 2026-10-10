"""Production config fail-closed validation."""

from __future__ import annotations

import pytest

from app.config import Config


def test_production_requires_auth_jwt_redis(monkeypatch):
    monkeypatch.setattr(Config, "ENVIRONMENT", "production")
    monkeypatch.setattr(Config, "AUTH_ENABLED", False)
    monkeypatch.setattr(Config, "JWT_SECRET_KEY", "")
    monkeypatch.setattr(Config, "REDIS_URL", "")
    monkeypatch.setattr(Config, "ALLOWED_ORIGINS", ["*"])

    with pytest.raises(RuntimeError) as exc:
        Config.validate_for_boot()
    message = str(exc.value)
    assert "AUTH_ENABLED" in message
    assert "JWT_SECRET_KEY" in message
    assert "REDIS_URL" in message


def test_development_allows_open_config(monkeypatch):
    monkeypatch.setattr(Config, "ENVIRONMENT", "development")
    monkeypatch.setattr(Config, "AUTH_ENABLED", False)
    monkeypatch.setattr(Config, "JWT_SECRET_KEY", "")
    monkeypatch.setattr(Config, "REDIS_URL", "")
    Config.validate_for_boot()  # must not raise
