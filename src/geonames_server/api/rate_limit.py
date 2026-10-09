"""Per-user request limits per UTC minute, hour and calendar month.

Redis holds one counter per user and window. One Lua script checks all three
and, only if none is used up, increments them and records the request in the
pending usage hash that the usage flush copies to Postgres. Limits count
across all of a user's keys; rejected requests do not count.

Postgres is the durable record: the usage flush copies the counts to
app.api_key_usage every few minutes. When a user's month counter is missing
(a new month, or Redis lost its data), their month and hour counters are
rebuilt from there before the request is counted.

If Redis fails, a warning is logged once and Redis is skipped for
BACKOFF_SECONDS, so requests do not each wait for a timeout. Meanwhile
LocalLimiter counts and limits in process memory, starting each user from
their flushed usage, and the usage flush writes its counts to Postgres. Once
Redis answers again, the flush writes everything and adds each outage user's
in-memory requests to their Redis counters. A user whose counters Redis lost
is rebuilt from the in-memory counts instead, which already include the
outage. One app instance runs, so one process's counters are all of them.
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.api.auth import Principal, require_api_key
from geonames_server.database.models.accounts import ApiKey, ApiKeyUsage
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
# Returns 0 when counted, 1-3 for the exhausted window (minute, hour, month),
# or -1 without counting when the month counter is missing and must be rebuilt
# from Postgres. The month is checked first, so the client is told the longest
# wait.
_SCRIPT = """
if redis.call('EXISTS', KEYS[3]) == 0 then
  return -1
end
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

# KEYS: minute, hour and month counters. ARGV: the requests to add to each, then
# the three TTLs. Adds nothing when the month counter is missing: the next
# request rebuilds the counters. Returns 1 when added, else 0.
_ADD_SCRIPT = """
if redis.call('EXISTS', KEYS[3]) == 0 then
  return 0
end
for i = 1, 3 do
  local n = tonumber(ARGV[i])
  if n > 0 and redis.call('INCRBY', KEYS[i], n) == n then
    redis.call('EXPIRE', KEYS[i], ARGV[3 + i])
  end
end
return 1
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
    month_start: datetime
    ends: tuple[int, int, int]  # epoch seconds: minute, hour, month

    @classmethod
    def at(cls, now: float) -> Windows:
        epoch = int(now)
        moment = datetime.fromtimestamp(epoch, timezone.utc)
        month_start = datetime(moment.year, moment.month, 1, tzinfo=timezone.utc)
        if moment.month == 12:
            next_month = datetime(moment.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_month = datetime(moment.year, moment.month + 1, 1, tzinfo=timezone.utc)
        return cls(
            minute=epoch // 60,
            hour=epoch // 3600,
            month=f"{moment.year:04d}{moment.month:02d}",
            month_start=month_start,
            ends=(
                (epoch // 60 + 1) * 60,
                (epoch // 3600 + 1) * 3600,
                int(next_month.timestamp()),
            ),
        )

    @property
    def hour_start(self) -> datetime:
        return datetime.fromtimestamp(self.hour * 3600, timezone.utc)

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


def _int(value) -> int | None:
    return None if value is None else int(value)


def limits_of(principal: Principal) -> list[int | None]:
    return [principal.per_minute, principal.per_hour, principal.per_month]


def usage_fields(key_id, hour: int) -> tuple[str, str]:
    return f"n:{key_id}:{hour}", f"t:{key_id}"


SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class Usage:
    """Requests per key and UTC hour, and each key's last request."""

    def __init__(self) -> None:
        self.requests: dict[tuple[uuid.UUID, datetime], int] = {}
        self.last_used: dict[uuid.UUID, datetime] = {}

    def __bool__(self) -> bool:
        return bool(self.requests or self.last_used)

    def total(self) -> int:
        return sum(self.requests.values())

    def add(self, other: Usage) -> None:
        for slot, n in other.requests.items():
            self.requests[slot] = self.requests.get(slot, 0) + n
        for key_id, at in other.last_used.items():
            self.last_used[key_id] = max(at, self.last_used.get(key_id, at))

    @classmethod
    def from_hash(cls, fields: dict[bytes, bytes]) -> Usage:
        """Parse a USAGE_PENDING hash: n:{key_id}:{epoch hour} and t:{key_id}."""
        usage = cls()
        for field, value in fields.items():
            kind, key_id, *hour = field.decode().split(":")
            if kind == "n":
                at = datetime.fromtimestamp(int(hour[0]) * 3600, timezone.utc)
                usage.requests[(uuid.UUID(key_id), at)] = int(value)
            elif kind == "t":
                usage.last_used[uuid.UUID(key_id)] = datetime.fromtimestamp(
                    int(value), timezone.utc
                )
        return usage


class LocalLimiter:
    """Counters kept in process memory while Redis is down.

    Same windows and rules as the Redis script. The caller loads a user's month
    and hour from Postgres before their first request.
    """

    def __init__(self) -> None:
        # Counter key (as in Redis) -> [count, end of its window, requests
        # counted here that are not in the Redis counter yet]
        self._counts: dict[str, list[int]] = {}
        self.usage = Usage()
        # Users counted here, whose requests must be added to Redis afterwards
        self.users: set[uuid.UUID] = set()

    def is_loaded(self, user_id, windows: Windows) -> bool:
        return windows.counter_keys(user_id)[2] in self._counts

    def load(self, user_id, windows: Windows, month: int, hour: int) -> None:
        keys = windows.counter_keys(user_id)
        self._counts.setdefault(keys[2], [month, windows.ends[2], 0])
        self._counts.setdefault(keys[1], [hour, windows.ends[1], 0])

    def counts(self, user_id, windows: Windows) -> list[int]:
        return [self._counts.get(key, [0])[0] for key in windows.counter_keys(user_id)]

    def unsent(self, user_id, windows: Windows) -> list[int]:
        """Requests counted here that the user's Redis counters lack."""
        return [
            self._counts[key][2] if key in self._counts else 0
            for key in windows.counter_keys(user_id)
        ]

    def hit(self, principal: Principal, windows: Windows, now: float) -> int | None:
        """Count a request; return the exhausted window (0-2) instead if any."""
        counts = self.counts(principal.user_id, windows)
        for window, limit in reversed(list(enumerate(limits_of(principal)))):
            if limit is not None and counts[window] >= limit:
                return window
        for window, key in enumerate(windows.counter_keys(principal.user_id)):
            counter = self._counts.setdefault(key, [0, windows.ends[window], 0])
            counter[0] += 1
            counter[2] += 1
        slot = (principal.key_id, windows.hour_start)
        self.usage.requests[slot] = self.usage.requests.get(slot, 0) + 1
        self.usage.last_used[principal.key_id] = datetime.fromtimestamp(
            int(now), timezone.utc
        )
        self.users.add(principal.user_id)
        return None

    def prune(self, now: float) -> None:
        """Forget counters whose window has ended."""
        for key in [k for k, (_, end, _) in self._counts.items() if end <= now]:
            del self._counts[key]

    def take_usage(self) -> Usage:
        usage, self.usage = self.usage, Usage()
        return usage

    def restore_usage(self, usage: Usage) -> None:
        """Put back usage that could not be written."""
        self.usage.add(usage)

    def forget(self, user_id) -> None:
        """Drop a user whose requests are in Redis now.

        Their in-memory counters go too: a later outage starts again from Postgres.
        """
        self.users.discard(user_id)
        prefix = f"rl:{user_id}:"
        for key in [k for k in self._counts if k.startswith(prefix)]:
            del self._counts[key]


async def stored_usage(
    session: AsyncSession, user_id: uuid.UUID, windows: Windows
) -> tuple[int, int]:
    """The user's requests in Postgres for the month and hour of windows."""
    requests = func.coalesce(func.sum(ApiKeyUsage.requests), 0)
    month, hour = (
        await session.execute(
            select(
                requests,
                func.coalesce(
                    func.sum(ApiKeyUsage.requests).filter(
                        ApiKeyUsage.hour == windows.hour_start
                    ),
                    0,
                ),
            )
            .join(ApiKey, ApiKey.id == ApiKeyUsage.key_id)
            .where(
                ApiKey.user_id == user_id,
                ApiKeyUsage.hour >= windows.month_start,
                ApiKeyUsage.hour
                < datetime.fromtimestamp(windows.ends[2], timezone.utc),
            )
        )
    ).one()
    return int(month), int(hour)


class RateLimiter:
    """Applies the limits through one Redis client; one per app."""

    def __init__(self, redis: Redis, session_factory: SessionFactory) -> None:
        self.redis = redis
        self._session_factory = session_factory
        self._script = redis.register_script(_SCRIPT)
        self._add_script = redis.register_script(_ADD_SCRIPT)
        # Held while outage counts move to Redis, so a user's are added once:
        # by a rebuild or by reconcile, never both.
        self._moving = asyncio.Lock()
        # Redis is skipped until this time (epoch seconds) after a failure.
        self._down_until = 0.0
        self.outage = False
        self.local = LocalLimiter()
        # Set when Redis answers again, to wake the usage flush for the reconcile
        self.recovered = asyncio.Event()

    def _redis_failed(self, error: Exception, now: float) -> None:
        if not self.outage:
            log.warning(
                f"Redis failed ({error!r}); counting and limiting in memory. "
                f"Retrying in {BACKOFF_SECONDS} s"
            )
        self.outage = True
        self._down_until = now + BACKOFF_SECONDS

    def _redis_ok(self) -> None:
        if self.outage:
            log.info("Redis is back; rate limit counters move back to Redis")
            self.outage = False
            self.recovered.set()

    async def _stored_usage(
        self, user_id: uuid.UUID, windows: Windows
    ) -> tuple[int, int] | None:
        try:
            async with self._session_factory() as session:
                return await stored_usage(session, user_id, windows)
        except Exception as e:
            log.warning(f"Could not read the stored usage from Postgres: {e!r}")
            return None

    async def _hit_locally(
        self, principal: Principal, windows: Windows, now: float
    ) -> int | None:
        if not self.local.is_loaded(principal.user_id, windows):
            # With Postgres down too, the user starts from 0.
            month, hour = await self._stored_usage(principal.user_id, windows) or (0, 0)
            self.local.load(principal.user_id, windows, month, hour)
        return self.local.hit(principal, windows, now)

    async def current_usage(
        self, user_id: uuid.UUID, windows: Windows, now: float
    ) -> tuple[list[int | None], bool]:
        """The user's minute, hour and month counts, and whether they are live.

        Live counts come from Redis. Otherwise the in-memory outage counts, or
        failing that the flushed usage, with no minute count.
        """
        if now >= self._down_until:
            try:
                minute, hour, month = await self.redis.mget(
                    windows.counter_keys(user_id)
                )
            except (RedisError, OSError) as e:
                self._redis_failed(e, now)
            else:
                self._redis_ok()
                if month is None:
                    # Not rebuilt yet: no request since the counters were lost
                    month, hour = await self._stored_usage(user_id, windows) or (
                        None,
                        None,
                    )
                return [int(minute or 0), _int(hour), _int(month)], True
        if self.local.is_loaded(user_id, windows):
            return list(self.local.counts(user_id, windows)), False
        month, hour = await self._stored_usage(user_id, windows) or (None, None)
        return [None, hour, month], False

    async def _rebuild(
        self, principal: Principal, windows: Windows, now: float
    ) -> bool:
        """Set the user's counters, unless set meanwhile.

        From the in-memory counts if the user has outage requests not in Redis
        yet (Postgres may lack them), else from Postgres.
        """
        user_id = principal.user_id
        keys, ttls = windows.counter_keys(user_id), windows.ttls(now)
        if user_id in self.local.users:
            async with self._moving:
                if user_id in self.local.users and self.local.is_loaded(
                    user_id, windows
                ):
                    counts = self.local.counts(user_id, windows)
                    async with self.redis.pipeline(transaction=False) as pipe:
                        for key, count, ttl in reversed(list(zip(keys, counts, ttls))):
                            pipe.set(key, count, nx=True, ex=ttl)
                        month_set, *_ = await pipe.execute()
                    if month_set:
                        self.local.forget(user_id)
                    return True
        stored = await self._stored_usage(user_id, windows)
        if stored is None:
            return False
        month, hour = stored
        async with self.redis.pipeline(transaction=False) as pipe:
            pipe.set(keys[2], month, nx=True, ex=ttls[2])
            pipe.set(keys[1], hour, nx=True, ex=ttls[1])
            await pipe.execute()
        return True

    async def reconcile(self) -> int:
        """Add the outage users' in-memory requests to their Redis counters.

        Call once their usage is in Postgres: a user whose month counter is
        missing is only dropped, and rebuilt from Postgres on their next request.
        Returns the users reconciled.
        """
        async with self._moving:
            users = list(self.local.users)
            if not users:
                return 0
            now = _now()
            windows = Windows.at(now)
            ttls = windows.ttls(now)
            async with self.redis.pipeline(transaction=False) as pipe:
                for user_id in users:
                    await self._add_script(
                        keys=windows.counter_keys(user_id),
                        args=[*self.local.unsent(user_id, windows), *ttls],
                        client=pipe,
                    )
                await pipe.execute()
            for user_id in users:
                self.local.forget(user_id)
            return len(users)

    async def hit(self, principal: Principal, now: float) -> int | None:
        """Count a request; return the exhausted window (0-2) instead if any."""
        windows = Windows.at(now)
        if now < self._down_until:
            return await self._hit_locally(principal, windows, now)
        usage_field, last_used_field = usage_fields(principal.key_id, windows.hour)
        keys = [*windows.counter_keys(principal.user_id), USAGE_PENDING]
        args = [
            *(-1 if limit is None else limit for limit in limits_of(principal)),
            *windows.ttls(now),
            usage_field,
            last_used_field,
            int(now),
        ]
        try:
            result = await self._script(keys=keys, args=args)
            if result == -1:
                if not await self._rebuild(principal, windows, now):
                    return None
                result = await self._script(keys=keys, args=args)
        except (RedisError, OSError) as e:
            self._redis_failed(e, now)
            return await self._hit_locally(principal, windows, now)
        self._redis_ok()
        # A second -1 (the counter vanished again) lets the request through.
        return int(result) - 1 if result > 0 else None


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
