"""Per-user request limits per UTC minute, hour and calendar month.

Redis holds one counter per user and window. One Lua script checks all three
and, only if none is used up, increments them and records the request in the
pending usage hash that the usage flush copies to Postgres. Limits count
across all of a user's keys; rejected requests do not count.

If Redis fails, requests go through: a warning is logged once and Redis is
skipped for BACKOFF_SECONDS, so requests do not each wait for a timeout.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from geonames_server.api.auth import Principal, require_api_key
from geonames_server.log.mylogger import get_logger

log = get_logger()

# Redis is skipped this long after a failed call.
BACKOFF_SECONDS = 30
# Counters outlive their window by this much, so a slow request near the end
# of a window still finds its counter.
_TTL_MARGIN = 60

# Hash of usage not yet copied to Postgres: n:{key_id}:{epoch hour} -> requests,
# t:{key_id} -> time of the key's last request (epoch seconds).
USAGE_PENDING = "usage:pending"

WINDOW_NAMES = ("minute", "hour", "month")

# KEYS: minute, hour and month counters, then the pending usage hash.
# ARGV: minute, hour and month limits (-1 = unlimited), the three TTLs, the
# usage field, the last-used field and the request time.
# Returns 0 when counted, or 1-3 for the exhausted window (minute, hour,
# month). The month is checked first, so the client is told the longest wait.
_SCRIPT = """
for i = 3, 1, -1 do
  local limit = tonumber(ARGV[i])
  if limit >= 0 and tonumber(redis.call('GET', KEYS[i]) or '0') >= limit then
    return i
  end
end
for i = 1, 3 do
  if redis.call('INCR', KEYS[i]) == 1 then
    redis.call('EXPIRE', KEYS[i], ARGV[3 + i])
  end
end
redis.call('HINCRBY', KEYS[4], ARGV[7], 1)
redis.call('HSET', KEYS[4], ARGV[8], ARGV[9])
return 0
"""


def _now() -> float:
    # Tests replace this to move between windows without waiting.
    return time.time()


@dataclass(frozen=True)
class Windows:
    """The UTC minute, hour and month a moment falls in, and when each ends."""

    minute: int  # epoch // 60
    hour: int  # epoch // 3600
    month: str  # YYYYMM
    ends: tuple[int, int, int]  # epoch seconds: minute, hour, month

    @classmethod
    def at(cls, now: float) -> Windows:
        epoch = int(now)
        moment = datetime.fromtimestamp(epoch, timezone.utc)
        if moment.month == 12:
            next_month = datetime(moment.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_month = datetime(moment.year, moment.month + 1, 1, tzinfo=timezone.utc)
        return cls(
            minute=epoch // 60,
            hour=epoch // 3600,
            month=f"{moment.year:04d}{moment.month:02d}",
            ends=(
                (epoch // 60 + 1) * 60,
                (epoch // 3600 + 1) * 3600,
                int(next_month.timestamp()),
            ),
        )

    def counter_keys(self, user_id) -> list[str]:
        return [
            f"rl:{user_id}:m:{self.minute}",
            f"rl:{user_id}:h:{self.hour}",
            f"rl:{user_id}:mo:{self.month}",
        ]

    def ttls(self, now: float) -> list[int]:
        return [math.ceil(end - now) + _TTL_MARGIN for end in self.ends]

    def retry_after(self, window: int, now: float) -> int:
        """Whole seconds until window (0 minute, 1 hour, 2 month) ends; at least 1."""
        return max(1, math.ceil(self.ends[window] - now))


def limits_of(principal: Principal) -> list[int | None]:
    return [principal.per_minute, principal.per_hour, principal.per_month]


def usage_fields(key_id, hour: int) -> tuple[str, str]:
    return f"n:{key_id}:{hour}", f"t:{key_id}"


class RateLimiter:
    """Applies the limits through one Redis client; one per app."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self._script = redis.register_script(_SCRIPT)
        # Redis is skipped until this time (epoch seconds) after a failure.
        self._down_until = 0.0
        self._outage = False

    def _redis_failed(self, error: Exception, now: float) -> None:
        if not self._outage:
            log.warning(
                f"Redis failed ({error!r}); rate limits are off and requests are "
                f"not counted. Retrying in {BACKOFF_SECONDS} s"
            )
        self._outage = True
        self._down_until = now + BACKOFF_SECONDS

    def _redis_ok(self) -> None:
        if self._outage:
            log.info("Redis is back; rate limits apply again")
        self._outage = False

    async def hit(self, principal: Principal, now: float) -> int | None:
        """Count a request; return the exhausted window (0-2) instead if any."""
        if now < self._down_until:
            return None
        windows = Windows.at(now)
        usage_field, last_used_field = usage_fields(principal.key_id, windows.hour)
        try:
            result = await self._script(
                keys=[*windows.counter_keys(principal.user_id), USAGE_PENDING],
                args=[
                    *(-1 if limit is None else limit for limit in limits_of(principal)),
                    *windows.ttls(now),
                    usage_field,
                    last_used_field,
                    int(now),
                ],
            )
        except (RedisError, OSError) as e:
            self._redis_failed(e, now)
            return None
        self._redis_ok()
        return None if result == 0 else int(result) - 1


def _limit_exceeded(principal: Principal, window: int, now: float) -> HTTPException:
    limit = limits_of(principal)[window]
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=(
            f"Rate limit exceeded: {limit} requests per {WINDOW_NAMES[window]} "
            f"on the {principal.tier} tier"
        ),
        headers={"Retry-After": str(Windows.at(now).retry_after(window, now))},
    )


async def enforce_rate_limit(
    request: Request, principal: Principal = Depends(require_api_key)
) -> Principal:
    """Count the request against the caller's limits, or answer 429."""
    limiter: RateLimiter | None = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        # REDIS_URL is not set; startup logged that limits are off.
        return principal
    now = _now()
    window = await limiter.hit(principal, now)
    if window is not None:
        raise _limit_exceeded(principal, window, now)
    return principal
