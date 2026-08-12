import redis

from app.config import get_settings

_settings = get_settings()
_redis: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(_settings.redis_url, decode_responses=True)
    return _redis
