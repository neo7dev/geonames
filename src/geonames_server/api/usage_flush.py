"""Copy the usage counted in Redis to Postgres every few minutes.

The rate limiter adds each request to the hash USAGE_PENDING. A flush renames
it to a batch key (new requests start a fresh hash) listed in the set BATCHES,
writes the batch to
app.api_key_usage and api_keys.last_used_at in one transaction that also
records the batch in app.usage_flushes, then deletes the batch key and its
BATCHES entry. A batch
whose write failed stays in Redis for the next flush; one that was written but
not deleted is found in usage_flushes and only deleted, so nothing counts twice.

While Redis is down, the rate limiter counts in memory; each flush writes
those counts too, recorded as a batch the same way. Once Redis answers again and the outage usage is in
Postgres, the flush adds the outage users' in-memory requests to their Redis
counters (RateLimiter.reconcile).
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.api import rate_limit
from geonames_server.api.rate_limit import (
    USAGE_PENDING,
    RateLimiter,
    SessionFactory,
    Usage,
)
from geonames_server.database.models.accounts import UsageFlush
from geonames_server.log.mylogger import get_logger

log = get_logger()

FLUSHING_PREFIX = "usage:flushing:"
# Set of the batch keys not yet deleted
BATCHES = "usage:batches"

# KEYS: the pending hash, the new batch key, BATCHES. Renames the pending hash
# (if any) to the batch key and lists it; returns all listed batches.
_TAKE_PENDING = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  redis.call('RENAME', KEYS[1], KEYS[2])
  redis.call('SADD', KEYS[3], KEYS[2])
end
return redis.call('SMEMBERS', KEYS[3])
"""

# Keys deleted since (their user was deleted) are dropped by the join instead of
# failing the batch on the foreign key.
_ADD_USAGE = text(
    """
    INSERT INTO app.api_key_usage (key_id, hour, requests)
    SELECT u.key_id, u.hour, u.requests
    FROM unnest(
        CAST(:key_ids AS uuid[]),
        CAST(:hours AS timestamptz[]),
        CAST(:requests AS bigint[])
    ) AS u (key_id, hour, requests)
    JOIN app.api_keys k ON k.id = u.key_id
    ON CONFLICT (key_id, hour)
    DO UPDATE SET requests = app.api_key_usage.requests + EXCLUDED.requests
    """
)

# greatest() ignores NULL, so a key's first use sets the column.
_SET_LAST_USED = text(
    """
    UPDATE app.api_keys k SET last_used_at = greatest(k.last_used_at, u.used_at)
    FROM unnest(CAST(:key_ids AS uuid[]), CAST(:used_at AS timestamptz[]))
        AS u (key_id, used_at)
    WHERE k.id = u.key_id
    """
)

_PRUNE_FLUSHES = text(
    "DELETE FROM app.usage_flushes WHERE flushed_at < now() - interval '7 days'"
)


async def write_usage(
    session: AsyncSession, usage: Usage, batch: str | None = None
) -> bool:
    """Add usage to Postgres in one transaction.

    With a batch name, the batch is recorded too, and False means it had been
    written before and nothing was added.
    """
    if batch is not None:
        recorded = await session.scalar(
            insert(UsageFlush)
            .values(batch=batch)
            .on_conflict_do_nothing()
            .returning(UsageFlush.batch)
        )
        if recorded is None:
            # Nothing was changed; end the transaction.
            await session.commit()
            return False
    if usage.requests:
        (key_ids, hours), requests = (
            zip(*usage.requests.keys()),
            list(usage.requests.values()),
        )
        await session.execute(
            _ADD_USAGE,
            {"key_ids": list(key_ids), "hours": list(hours), "requests": requests},
        )
    if usage.last_used:
        await session.execute(
            _SET_LAST_USED,
            {
                "key_ids": list(usage.last_used.keys()),
                "used_at": list(usage.last_used.values()),
            },
        )
    await session.execute(_PRUNE_FLUSHES)
    await session.commit()
    return True


async def _flush_redis(redis: Redis, session_factory: SessionFactory) -> int:
    # This flush's batch, plus any a failed flush left behind
    batches = await redis.register_script(_TAKE_PENDING)(
        keys=[USAGE_PENDING, f"{FLUSHING_PREFIX}{uuid.uuid4()}", BATCHES]
    )
    written = 0
    for name in batches:
        usage = Usage.from_hash(await redis.hgetall(name))
        if usage:
            async with session_factory() as session:
                if await write_usage(session, usage, batch=name.decode()):
                    written += usage.total()
        async with redis.pipeline(transaction=True) as pipe:
            await pipe.delete(name).srem(BATCHES, name).execute()
    return written


async def _flush_local(limiter: RateLimiter, session_factory: SessionFactory) -> int:
    written = 0
    # A batch a failed flush left behind, then the usage counted since
    for _ in range(2):
        batch = limiter.local.take_usage()
        if batch is None:
            break
        name, usage = batch
        async with session_factory() as session:
            if await write_usage(session, usage, batch=name):
                written += usage.total()
        limiter.local.written()
    return written


async def _reconcile(limiter: RateLimiter) -> None:
    users = await limiter.reconcile()
    if users:
        log.info(f"Added the outage requests of {users} user(s) to Redis")


async def flush_usage(
    redis: Redis, session_factory: SessionFactory, limiter: RateLimiter | None = None
) -> int:
    """Write the pending usage to Postgres; return the requests written.

    Redis's usage first, then the limiter's in-memory usage (written even with
    Redis down). Once both are in and Redis is answering, the outage's
    counters are reconciled.
    """
    redis_error: Exception | None = None
    try:
        written = await _flush_redis(redis, session_factory)
    except (RedisError, OSError) as e:
        redis_error, written = e, 0
    if limiter is not None:
        written += await _flush_local(limiter, session_factory)
        limiter.local.prune(rate_limit._now())
        if redis_error is None and not limiter.outage:
            await _reconcile(limiter)
    if redis_error is not None:
        raise redis_error
    return written


async def run_usage_flusher(
    redis: Redis,
    session_factory: SessionFactory,
    interval: float,
    limiter: RateLimiter | None = None,
) -> None:
    """Flush every interval seconds until cancelled; failures are retried next time.

    Redis coming back after an outage wakes it at once, to reconcile.
    """
    while True:
        if limiter is None:
            await asyncio.sleep(interval)
        else:
            with suppress(TimeoutError):
                await asyncio.wait_for(limiter.recovered.wait(), interval)
            limiter.recovered.clear()
        try:
            written = await flush_usage(redis, session_factory, limiter)
            log.debug(f"Usage flush wrote {written} request(s)")
        except Exception as e:
            log.warning(f"Usage flush failed, retrying in {interval} s: {e!r}")
