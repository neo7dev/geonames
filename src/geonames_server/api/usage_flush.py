"""Copy the usage counted in Redis to Postgres every few minutes.

The rate limiter adds each request to the hash USAGE_PENDING. A flush renames
it to a batch key (new requests start a fresh hash), writes the batch to
app.api_key_usage and api_keys.last_used_at in one transaction that also
records the batch in app.usage_flushes, then deletes the batch key. A batch
whose write failed stays in Redis for the next flush; one that was written but
not deleted is found in usage_flushes and only deleted, so nothing counts twice.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.api.rate_limit import USAGE_PENDING
from geonames_server.database.models.accounts import UsageFlush
from geonames_server.log.mylogger import get_logger

log = get_logger()

FLUSHING_PREFIX = "usage:flushing:"

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

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


class Usage:
    """Requests per key and UTC hour, and each key's last request."""

    def __init__(self) -> None:
        self.requests: dict[tuple[uuid.UUID, datetime], int] = {}
        self.last_used: dict[uuid.UUID, datetime] = {}

    def __bool__(self) -> bool:
        return bool(self.requests or self.last_used)

    def total(self) -> int:
        return sum(self.requests.values())

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


async def flush_usage(redis: Redis, session_factory: SessionFactory) -> int:
    """Write Redis's pending usage to Postgres; return the requests written."""
    try:
        await redis.rename(USAGE_PENDING, f"{FLUSHING_PREFIX}{uuid.uuid4()}")
    except ResponseError as e:
        if "no such key" not in str(e).lower():
            raise
    # This flush's batch, plus any a failed flush left behind
    batches = [name async for name in redis.scan_iter(match=f"{FLUSHING_PREFIX}*")]
    written = 0
    for name in batches:
        usage = Usage.from_hash(await redis.hgetall(name))
        async with session_factory() as session:
            if await write_usage(session, usage, batch=name.decode()):
                written += usage.total()
        await redis.delete(name)
    return written


async def run_usage_flusher(
    redis: Redis, session_factory: SessionFactory, interval: float
) -> None:
    """Flush every interval seconds until cancelled; failures are retried next time."""
    while True:
        await asyncio.sleep(interval)
        try:
            written = await flush_usage(redis, session_factory)
            log.debug(f"Usage flush wrote {written} request(s)")
        except Exception as e:
            log.warning(f"Usage flush failed, retrying in {interval} s: {e!r}")
