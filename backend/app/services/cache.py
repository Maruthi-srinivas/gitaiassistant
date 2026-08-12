from __future__ import annotations

import hashlib
import json

from app.redis_client import get_redis


def cache_get(key: str) -> dict | None:
    raw = get_redis().get(f"cache:{key}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def cache_set(key: str, value: dict, ttl_seconds: int = 300) -> None:
    get_redis().setex(f"cache:{key}", ttl_seconds, json.dumps(value))


def chat_cache_key(repo_id: str, message: str) -> str:
    digest = hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]
    return f"chat:{repo_id}:{digest}"
