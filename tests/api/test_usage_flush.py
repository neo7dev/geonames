"""The usage flush copies Redis's request counts to Postgres exactly once.

Needs TEST_REDIS_URL and the Supabase database the other API tests use.
"""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from geonames_server.api import usage_flush
from geonames_server.api.rate_limit import USAGE_PENDING
from geonames_server.api.usage_flush import FLUSHING_PREFIX, flush_usage
from geonames_server.database.models.accounts import ApiKey, ApiKeyUsage, UsageFlush

PATH = "/metadata/"


@pytest.fixture
def session_factory(async_session):
    """Hands out the test's session, so flushed rows roll back with the test."""

    @asynccontextmanager
    async def factory():
        yield async_session

    return factory


async def _key(make_user, make_key, async_session) -> tuple[str, uuid.UUID]:
    key = await make_key(await make_user("free"))
    key_id = (
        await async_session.scalars(
            select(ApiKey.id).order_by(ApiKey.created_at.desc())
        )
    ).first()
    return key, key_id


async def _usage_rows(async_session, key_id) -> list[tuple[datetime, int]]:
    rows = await async_session.execute(
        select(ApiKeyUsage.hour, ApiKeyUsage.requests)
        .where(ApiKeyUsage.key_id == key_id)
        .order_by(ApiKeyUsage.hour)
    )
    return [tuple(row) for row in rows]


def _hour(clock_now: float, hours_later: int = 0) -> datetime:
    return datetime.fromtimestamp(
        (int(clock_now) // 3600 + hours_later) * 3600, timezone.utc
    )


@pytest.mark.asyncio
async def test_calls_in_two_hours_give_two_rows_and_set_last_used(
    auth_client,
    limiter,
    redis,
    session_factory,
    async_session,
    make_user,
    make_key,
    clock,
):
    key, key_id = await _key(make_user, make_key, async_session)
    start = clock.now
    for _ in range(3):
        await auth_client.get(PATH, headers={"X-API-Key": key})
    clock.now += 3600
    await auth_client.get(PATH, headers={"X-API-Key": key})

    assert await flush_usage(redis, session_factory) == 4

    assert await _usage_rows(async_session, key_id) == [
        (_hour(start), 3),
        (_hour(start, 1), 1),
    ]
    last_used = await async_session.scalar(
        select(ApiKey.last_used_at).where(ApiKey.id == key_id)
    )
    assert last_used == datetime.fromtimestamp(int(clock.now), timezone.utc)
    assert await redis.keys("usage:*") == []


@pytest.mark.asyncio
async def test_flushes_add_up_and_an_empty_flush_adds_nothing(
    auth_client,
    limiter,
    redis,
    session_factory,
    async_session,
    make_user,
    make_key,
    clock,
):
    key, key_id = await _key(make_user, make_key, async_session)
    await auth_client.get(PATH, headers={"X-API-Key": key})
    await flush_usage(redis, session_factory)
    await auth_client.get(PATH, headers={"X-API-Key": key})
    await flush_usage(redis, session_factory)

    assert await flush_usage(redis, session_factory) == 0
    assert await _usage_rows(async_session, key_id) == [(_hour(clock.now), 2)]


@pytest.mark.asyncio
async def test_a_failed_write_keeps_the_batch_for_the_next_flush(
    auth_client,
    limiter,
    redis,
    session_factory,
    async_session,
    make_user,
    make_key,
    clock,
):
    key, key_id = await _key(make_user, make_key, async_session)
    await auth_client.get(PATH, headers={"X-API-Key": key})

    with patch.object(
        usage_flush, "write_usage", AsyncMock(side_effect=OSError("db down"))
    ):
        with pytest.raises(OSError):
            await flush_usage(redis, session_factory)
    assert len(await redis.keys(f"{FLUSHING_PREFIX}*")) == 1

    await auth_client.get(PATH, headers={"X-API-Key": key})
    assert await flush_usage(redis, session_factory) == 2
    assert await _usage_rows(async_session, key_id) == [(_hour(clock.now), 2)]
    assert await redis.keys("usage:*") == []


@pytest.mark.asyncio
async def test_a_batch_written_before_is_only_deleted(
    redis, session_factory, async_session, make_user, make_key, clock
):
    _, key_id = await _key(make_user, make_key, async_session)
    batch = f"{FLUSHING_PREFIX}already-written"
    await redis.hset(batch, f"n:{key_id}:{int(clock.now) // 3600}", 5)
    async_session.add(UsageFlush(batch=batch))
    await async_session.commit()

    assert await flush_usage(redis, session_factory) == 0

    assert await _usage_rows(async_session, key_id) == []
    assert await redis.keys("usage:*") == []


@pytest.mark.asyncio
async def test_a_deleted_keys_counts_are_dropped(
    redis, session_factory, async_session, make_user, make_key, clock
):
    _, key_id = await _key(make_user, make_key, async_session)
    hour = int(clock.now) // 3600
    gone = uuid.uuid4()
    await redis.hset(
        USAGE_PENDING,
        mapping={
            f"n:{key_id}:{hour}": 2,
            f"n:{gone}:{hour}": 7,
            f"t:{gone}": int(clock.now),
        },
    )

    await flush_usage(redis, session_factory)

    assert await _usage_rows(async_session, key_id) == [(_hour(clock.now), 2)]
    assert await _usage_rows(async_session, gone) == []


@pytest.mark.asyncio
async def test_old_flush_records_are_pruned(redis, session_factory, async_session):
    async_session.add(
        UsageFlush(
            batch="old", flushed_at=datetime.now(timezone.utc) - timedelta(days=8)
        )
    )
    async_session.add(UsageFlush(batch="recent"))
    await async_session.commit()
    await redis.hset(USAGE_PENDING, "t:" + str(uuid.uuid4()), 1)

    await flush_usage(redis, session_factory)

    batches = set(await async_session.scalars(select(UsageFlush.batch)))
    assert "old" not in batches
    assert "recent" in batches


@pytest.mark.asyncio
async def test_the_flusher_keeps_running_after_a_failure():
    flush = AsyncMock(side_effect=[OSError("db down"), 3, 3])
    with patch.object(usage_flush, "flush_usage", flush):
        task = asyncio.create_task(usage_flush.run_usage_flusher(None, None, 0.01))
        while flush.await_count < 3:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
