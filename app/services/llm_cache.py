"""
Redis-кэш для LLM-объяснений мэтчей.

Ключ: llm:explain:{sha1(rk_a:rk_b)[:16]}
TTL:  7 дней

При любой ошибке подключения к Redis функции молча возвращают None / ничего не делают,
чтобы не ломать основной флоу.
"""

import hashlib
import os

import redis.asyncio as aioredis

_REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
_TTL: int = 7 * 24 * 3600

_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(_REDIS_URL, decode_responses=True)
    return _redis


def _cache_key(rk_a: str, rk_b: str) -> str:
    digest = hashlib.sha1(f"{rk_a}:{rk_b}".encode()).hexdigest()[:16]
    return f"llm:explain:{digest}"


async def get_cached(rk_a: str, rk_b: str) -> str | None:
    """Возвращает закэшированное объяснение или None."""
    try:
        return await _get_redis().get(_cache_key(rk_a, rk_b))
    except Exception:
        return None


async def set_cached(rk_a: str, rk_b: str, explanation: str) -> None:
    """Кладёт объяснение в кэш с TTL 7 дней."""
    try:
        await _get_redis().set(_cache_key(rk_a, rk_b), explanation, ex=_TTL)
    except Exception:
        pass
