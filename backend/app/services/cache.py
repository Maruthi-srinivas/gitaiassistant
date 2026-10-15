from __future__ import annotations

import hashlib
import json
import logging

from app.redis_client import get_redis

logger = logging.getLogger(__name__)


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


def chat_cache_key(repo_id: str, message: str, commit_hash: str | None = None) -> str:
    digest = hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]
    commit_seg = (commit_hash or "none").strip()[:16] or "none"
    return f"chat:{repo_id}:{commit_seg}:{digest}"


def invalidate_repo_chat_cache(repo_id: str) -> int:
    """Delete all cached chat responses for a repository."""
    try:
        r = get_redis()
        keys = list(r.scan_iter(match=f"cache:chat:{repo_id}:*"))
        if not keys:
            return 0
        return int(r.delete(*keys) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to invalidate chat cache for repo %s: %s", repo_id, exc)
        return 0
