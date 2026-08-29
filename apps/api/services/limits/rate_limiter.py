from __future__ import annotations

import logging

import redis.asyncio as aioredis

from apps.api.config import settings

logger = logging.getLogger("inferra.redis")

_redis_pool: aioredis.ConnectionPool | None = None


def get_redis_pool() -> aioredis.ConnectionPool:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.ConnectionPool.from_url(
            settings.redis_url,
            max_connections=50,
            decode_responses=True,
        )
    return _redis_pool


def get_redis() -> aioredis.Redis:
    return aioredis.Redis(connection_pool=get_redis_pool())


RATE_LIMIT_LUA = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
end
if current > tonumber(ARGV[1]) then
    return 0
end
return 1
"""


async def check_rpm_limit(org_id: str, limit: int, window_seconds: int = 60) -> bool:
    redis = get_redis()
    key = f"rl:rpm:{org_id}"
    try:
        result = await redis.eval(RATE_LIMIT_LUA, 1, key, limit, window_seconds)
        return bool(result)
    except Exception:
        logger.exception("Redis RPM check failed; failing open")
        return True


class ConcurrencyTracker:
    async def acquire(self, org_id: str, max_concurrent: int) -> bool:
        redis = get_redis()
        key = f"rl:concurrent:{org_id}"
        try:
            current = await redis.incr(key)
            await redis.expire(key, 300)
            if current > max_concurrent:
                await redis.decr(key)
                return False
            return True
        except Exception:
            logger.exception("Redis concurrent check failed; failing open")
            return True

    async def release(self, org_id: str) -> None:
        redis = get_redis()
        key = f"rl:concurrent:{org_id}"
        try:
            await redis.decr(key)
        except Exception:
            logger.exception("Redis concurrent release failed")


concurrency_tracker = ConcurrencyTracker()


async def check_global_queue(limit: int) -> bool:
    redis = get_redis()
    key = "rl:queue_depth"
    try:
        current = int(await redis.get(key) or 0)
        if current >= limit:
            return False
        new_depth = await redis.incr(key)
        try:
            from apps.api.services.observability.metrics import global_queue_depth
            global_queue_depth.set(new_depth)
        except Exception:
            pass
        return True
    except Exception:
        logger.exception("Redis queue check failed; failing open")
        return True


async def release_global_queue() -> None:
    redis = get_redis()
    try:
        new_depth = await redis.decr("rl:queue_depth")
        try:
            from apps.api.services.observability.metrics import global_queue_depth
            global_queue_depth.set(max(0, new_depth))
        except Exception:
            pass
    except Exception:
        logger.exception("Redis queue release failed")


async def check_daily_quota(org_id: str, estimated_tokens: int, hard_limit: int | None) -> bool:
    if hard_limit is None:
        return True
    redis = get_redis()
    from datetime import datetime

    date_key = datetime.utcnow().strftime("%Y-%m-%d")
    key = f"rl:daily_tokens:{org_id}:{date_key}"
    try:
        current = int(await redis.get(key) or 0)
        return current + estimated_tokens <= hard_limit
    except Exception:
        logger.exception("Redis daily quota check failed; failing closed")
        return False


async def increment_token_usage(org_id: str, tokens_used: int) -> None:
    redis = get_redis()
    from datetime import datetime

    date_key = datetime.utcnow().strftime("%Y-%m-%d")
    key = f"rl:daily_tokens:{org_id}:{date_key}"
    try:
        await redis.incrby(key, tokens_used)
        await redis.expire(key, 90000)
    except Exception:
        logger.exception("Redis token increment failed")
