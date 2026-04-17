from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Optional, cast

from redis import asyncio as redis

_LOGGER = logging.getLogger(__name__)

_redis_client: Optional[redis.Redis] = None


def _get_redis_url() -> str:
    # Fallback aligns with docker-compose default.
    return os.getenv("REDIS_URL", "redis://redis:6379/0")


def _get_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(_get_redis_url(), decode_responses=True)
    return _redis_client


def _day_key(bucket: str, user_id: int, now: datetime) -> str:
    return f"antiabuse:{bucket}:{user_id}:{now.date().isoformat()}"


def _seconds_until_tomorrow(now: datetime) -> int:
    tomorrow = (now + timedelta(days=1)).date()
    midnight = datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc)
    return max(1, int((midnight - now).total_seconds()))


async def get_daily_count(user_id: int, bucket: str) -> int:
    now = datetime.now(timezone.utc)
    key = _day_key(bucket, user_id, now)
    try:
        raw = await _get_client().get(key)
        return int(raw or 0)
    except Exception:
        _LOGGER.exception("anti-abuse: failed to read redis counter")
        return 0


async def check_and_incr_daily(
    user_id: int, bucket: str, amount: int, limit: int
) -> tuple[bool, int]:
    now = datetime.now(timezone.utc)
    key = _day_key(bucket, user_id, now)
    ttl = _seconds_until_tomorrow(now)
    script = """
    local key = KEYS[1]
    local amount = tonumber(ARGV[1])
    local limit = tonumber(ARGV[2])
    local ttl = tonumber(ARGV[3])
    local current = tonumber(redis.call("GET", key) or "0")
    local new = current + amount
    if new > limit then
        return {0, current}
    end
    if current == 0 then
        redis.call("SET", key, new, "EX", ttl)
    else
        redis.call("INCRBY", key, amount)
    end
    return {1, new}
    """
    try:
        result = cast(
            Awaitable[Any],
            _get_client().eval(script, 1, key, str(amount), str(limit), str(ttl)),
        )
        ok, value = await result
        return bool(ok), int(value)
    except Exception:
        _LOGGER.exception("anti-abuse: failed to update redis counter")
        return True, 0
