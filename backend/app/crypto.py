from __future__ import annotations

import base64
import hashlib
import logging
import re

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

logger = logging.getLogger(__name__)

_ENC_PREFIX = "enc:v1:"

_SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{8,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{8,}"),
    re.compile(r"(https?://)[^@\s/]+@github\.com", re.IGNORECASE),
    re.compile(r"(Authorization:\s*(?:Basic|Bearer)\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
]


def _get_fernet() -> Fernet:
    settings = get_settings()
    raw_key = settings.encryption_key.strip()
    if raw_key:
        try:
            return Fernet(raw_key.encode("utf-8"))
        except Exception:
            derived = base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode("utf-8")).digest())
            return Fernet(derived)
    fallback_seed = settings.jwt_secret or "dev-jwt-secret-change-me"
    derived = base64.urlsafe_b64encode(hashlib.sha256(fallback_seed.encode("utf-8")).digest())
    return Fernet(derived)


def encrypt_secret(plaintext: str | None) -> str | None:
    if plaintext is None:
        return None
    cleaned = plaintext.strip()
    if not cleaned:
        return None
    if cleaned.startswith(_ENC_PREFIX):
        return cleaned
    token = _get_fernet().encrypt(cleaned.encode("utf-8")).decode("utf-8")
    return f"{_ENC_PREFIX}{token}"


def decrypt_secret(ciphertext: str | None) -> str | None:
    if ciphertext is None:
        return None
    cleaned = ciphertext.strip()
    if not cleaned:
        return None
    if not cleaned.startswith(_ENC_PREFIX):
        return cleaned
    payload = cleaned[len(_ENC_PREFIX) :]
    try:
        return _get_fernet().decrypt(payload.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.warning("Failed to decrypt stored secret token (invalid key or corrupted ciphertext)")
        return None


def redact_secrets(text: str | None, *secrets: str | None) -> str:
    if not text:
        return ""
    result = str(text)
    for sec in secrets:
        if not sec:
            continue
        plain = decrypt_secret(sec) if sec.startswith(_ENC_PREFIX) else sec
        for candidate in (sec.strip(), (plain or "").strip()):
            if candidate and len(candidate) >= 4:
                result = result.replace(candidate, "***")
                b64_basic = base64.b64encode(f"x-access-token:{candidate}".encode("utf-8")).decode(
                    "ascii"
                )
                result = result.replace(b64_basic, "***")
    result = _SECRET_PATTERNS[0].sub("gh***", result)
    result = _SECRET_PATTERNS[1].sub("github_pat_***", result)
    result = _SECRET_PATTERNS[2].sub(r"\1***@github.com", result)
    result = _SECRET_PATTERNS[3].sub(r"\1***", result)
    return result
