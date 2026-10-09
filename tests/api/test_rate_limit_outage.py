"""While Redis is down the limiter counts and limits in memory; afterwards the
outage users' counters are rebuilt from Postgres with the outage included.

Needs TEST_REDIS_URL and the Supabase database the other API tests use.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import func, select

from geonames_server.api import rate_limit, usage_flush
from geonames_server.api.app import app
from geonames_server.api.rate_limit import (
    BACKOFF_SECONDS,
    LocalLimiter,
    RateLimiter,
    Windows,
)
from geonames_server.api.usage_flush import flush_usage
from geonames_server.database.models.accounts import ApiKey, ApiKeyUsage

PATH = "/metadata/"


async def _calls(client, key: str, n: int) -> list[int]:
    return [
        (await client.get(PATH, headers={"X-API-Key": key})).status_code
        for _ in range(n)
    ]


async def _stored_total(async_session, user_id) -> int:
    return await async_session.scalar(
        select(func.coalesce(func.sum(ApiKeyUsage.requests), 0))
        .join(ApiKey, ApiKey.id == ApiKeyUsage.key_id)
        .where(ApiKey.user_id == user_id)
    )


@pytest_asyncio.fixture
async def down_limiter(auth_client, clock, session_factory):
    """Rate limiting against a Redis that refuses connections."""
    down = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=1)
    limiter = RateLimiter(down, session_factory)
    app.state.rate_limiter = limiter
    yield limiter
    del app.state.rate_limiter
    await down.aclose()


@pytest.fixture
def break_redis(limiter):
    """Make the limiter's Redis calls fail until the returned switch is flipped."""
    state = SimpleNamespace(broken=True)
    script = limiter._script

    async def maybe_failing(**kwargs):
        if state.broken:
            raise RedisConnectionError("Redis is down")
        return await script(**kwargs)

    with patch.object(limiter, "_script", side_effect=maybe_failing):
        yield state


@pytest.mark.asyncio
async def test_limits_hold_in_memory_and_redis_is_skipped(
    auth_client, down_limiter, make_user, make_key, clock
):
    key = await make_key(await make_user("free"))

    with (
        patch.object(down_limiter, "_script", wraps=down_limiter._script) as script,
        patch.object(rate_limit.log, "warning") as warning,
    ):
        assert await _calls(auth_client, key, 11) == [200] * 10 + [429]
        # The first call failed; the rest skipped Redis
        assert script.call_count == 1
        warning.assert_called_once()

        clock.now += BACKOFF_SECONDS
        await _calls(auth_client, key, 1)
        assert script.call_count == 2
        warning.assert_called_once()


@pytest.mark.asyncio
async def test_429_in_memory_has_retry_after(
    auth_client, down_limiter, make_user, make_key, clock
):
    key = await make_key(await make_user("free"))
    await _calls(auth_client, key, 10)

    resp = await auth_client.get(PATH, headers={"X-API-Key": key})

    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "30"


@pytest.mark.asyncio
async def test_each_user_starts_from_their_flushed_usage(
    auth_client, down_limiter, async_session, make_user, make_key, clock
):
    user_id = await make_user("free")
    key = await make_key(user_id)
    key_id = await async_session.scalar(
        select(ApiKey.id).where(ApiKey.user_id == user_id)
    )
    async_session.add(
        ApiKeyUsage(
            key_id=key_id,
            hour=datetime(2026, 10, 2, tzinfo=timezone.utc),
            requests=4999,
        )
    )
    await async_session.commit()

    resp = await auth_client.get(PATH, headers={"X-API-Key": key})
    assert resp.status_code == 200
    resp = await auth_client.get(PATH, headers={"X-API-Key": key})
    assert resp.status_code == 429
    assert "per month" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_outage_calls_are_flushed_with_redis_still_down(
    auth_client, down_limiter, async_session, session_factory, make_user, make_key
):
    user_id = await make_user("free")
    key = await make_key(user_id)
    await _calls(auth_client, key, 3)

    with pytest.raises(RedisConnectionError):
        await flush_usage(down_limiter.redis, session_factory, down_limiter)

    assert await _stored_total(async_session, user_id) == 3
    assert not down_limiter.local.usage
    last_used = await async_session.scalar(
        select(ApiKey.last_used_at).where(ApiKey.user_id == user_id)
    )
    assert last_used is not None


@pytest.mark.asyncio
async def test_with_postgres_down_too_counts_wait_in_memory(
    auth_client, down_limiter, async_session, session_factory, make_user, make_key
):
    user_id = await make_user("free")
    key = await make_key(user_id)

    @asynccontextmanager
    async def broken_factory():
        raise OSError("db down")
        yield

    down_limiter._session_factory = broken_factory
    # Starts from 0, but still limited per minute in memory
    assert await _calls(auth_client, key, 11) == [200] * 10 + [429]

    with pytest.raises(OSError):
        await flush_usage(down_limiter.redis, broken_factory, down_limiter)
    assert down_limiter.local.usage.total() == 10

    with pytest.raises(RedisConnectionError):
        await flush_usage(down_limiter.redis, session_factory, down_limiter)
    assert await _stored_total(async_session, user_id) == 10


@pytest.mark.asyncio
async def test_after_the_outage_counters_include_the_outage_calls(
    auth_client,
    limiter,
    break_redis,
    redis,
    async_session,
    session_factory,
    make_user,
    make_key,
    clock,
):
    user_id = await make_user("free")
    key = await make_key(user_id)
    break_redis.broken = False
    assert await _calls(auth_client, key, 2) == [200, 200]  # In Redis

    break_redis.broken = True
    assert await _calls(auth_client, key, 3) == [200] * 3  # In memory

    break_redis.broken = False
    clock.now += BACKOFF_SECONDS
    assert await _calls(auth_client, key, 1) == [200]  # Redis again
    assert limiter.recovered.is_set()

    assert await flush_usage(redis, session_factory, limiter) == 6
    assert await _stored_total(async_session, user_id) == 6
    windows = Windows.at(clock.now)
    month_key = windows.counter_keys(user_id)[2]
    assert await redis.exists(month_key) == 0

    assert await _calls(auth_client, key, 1) == [200]
    # Rebuilt from Postgres (6), plus this call
    assert int(await redis.get(month_key)) == 7
    assert limiter.local.users == set()


@pytest.mark.asyncio
async def test_counters_stay_when_the_write_fails_and_the_next_flush_finishes(
    auth_client,
    limiter,
    break_redis,
    redis,
    async_session,
    session_factory,
    make_user,
    make_key,
    clock,
):
    user_id = await make_user("free")
    key = await make_key(user_id)
    await _calls(auth_client, key, 2)  # In memory
    break_redis.broken = False
    clock.now += BACKOFF_SECONDS
    await _calls(auth_client, key, 1)
    month_key = Windows.at(clock.now).counter_keys(user_id)[2]

    real_write = usage_flush.write_usage
    with patch.object(
        usage_flush,
        "write_usage",
        AsyncMock(side_effect=[True, OSError("db down")]),
    ):
        with pytest.raises(OSError):
            await flush_usage(redis, session_factory, limiter)
    assert await redis.exists(month_key) == 1
    assert limiter.local.users == {user_id}

    with patch.object(usage_flush, "write_usage", real_write):
        await flush_usage(redis, session_factory, limiter)
    assert await redis.exists(month_key) == 0
    assert await _stored_total(async_session, user_id) == 2


@pytest.mark.asyncio
async def test_the_flusher_wakes_when_redis_comes_back():
    limiter = SimpleNamespace(recovered=asyncio.Event())
    flush = AsyncMock(return_value=0)
    with patch.object(usage_flush, "flush_usage", flush):
        task = asyncio.create_task(
            usage_flush.run_usage_flusher(None, None, 3600, limiter)
        )
        await asyncio.sleep(0.01)
        flush.assert_not_awaited()

        limiter.recovered.set()
        for _ in range(100):
            if flush.await_count:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    flush.assert_awaited_once()
    assert not limiter.recovered.is_set()


def test_ended_windows_are_forgotten():
    local = LocalLimiter()
    now = datetime(2026, 10, 9, 12, 0, 30, tzinfo=timezone.utc).timestamp()
    windows = Windows.at(now)
    local.load("u", windows, month=5, hour=2)

    local.prune(now + 3600)

    assert local.counts("u", Windows.at(now + 3600)) == [0, 0, 5]
    assert not local.is_loaded("u", Windows.at(now + 31 * 86400))
