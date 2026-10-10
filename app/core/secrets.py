"""Encrypted storage for LLM provider API keys.

Keys are stored Fernet-encrypted. Any configured secret string works: it is
derived to a valid 32-byte Fernet key via SHA-256, so operators can reuse
JWT_SECRET_KEY or set a dedicated LLM_KEY_ENCRYPTION_KEY. In dev with no
secret configured, an insecure fallback key is used and a warning is logged
(never do this in production).
"""

from __future__ import annotations

import base64
import hashlib
import logging

logger = logging.getLogger(__name__)

_DEV_FALLBACK_SECRET = "perfect-engine-dev-insecure-key-change-me"


def _derive_fernet_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _get_fernet():
    from cryptography.fernet import Fernet

    from app.config import Config

    secret = (Config.LLM_KEY_ENCRYPTION_KEY or "").strip()
    if not secret:
        logger.warning(
            "LLM_KEY_ENCRYPTION_KEY/JWT_SECRET_KEY not set; using insecure dev "
            "fallback for LLM key encryption. Set LLM_KEY_ENCRYPTION_KEY in "
            "production."
        )
        secret = _DEV_FALLBACK_SECRET
    return Fernet(_derive_fernet_key(secret))


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a non-empty secret; returns '' for empty input."""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a stored value; returns '' for empty input."""
    if not ciphertext:
        return ""
    return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")


def mask_key(api_key: str) -> str:
    """Safe hint for API responses, e.g. '****abcd'."""
    if not api_key:
        return ""
    cleaned = api_key.strip()
    if len(cleaned) <= 4:
        return "****"
    return f"****{cleaned[-4:]}"
