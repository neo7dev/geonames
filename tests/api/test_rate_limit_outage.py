"""While Redis is down the limiter counts and limits in memory; afterwards the
outage requests are added to the users' Redis counters.

Needs TEST_REDIS_URL and the Supabase database the other API tests use.
"""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
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
@pytest.mark.parametrize(
    ("error", "counted_in_memory"),
    [
        ("Timeout reading from redis:6379", 0),
        ("Timeout writing to socket", 0),
        ("Timeout connecting to server", 1),
    ],
)
async def test_a_reply_that_timed_out_is_not_counted_again_in_memory(
    auth_client, limiter, make_user, make_key, error, counted_in_memory
):
    key = await make_key(await make_user("free"))

    with patch.object(limiter, "_script", side_effect=RedisTimeoutError(error)):
        assert await _calls(auth_client, key, 1) == [200]

    assert limiter.outage
    assert limiter.local.usage.total() == counted_in_memory


@pytest.mark.asyncio
async def test_after_the_backoff_one_request_tries_redis(
    auth_client, down_limiter, make_user, make_key, clock
):
    key = await make_key(await make_user("free"))
    await _calls(auth_client, key, 1)
    clock.now += BACKOFF_SECONDS

    with patch.object(down_limiter, "_script", wraps=down_limiter._script) as script:
        statuses = await asyncio.gather(
            *(_calls(auth_client, key, 1) for _ in range(3))
        )

    assert statuses == [[200]] * 3
    script.assert_called_once()


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
        await flush_usage(down_limiter, session_factory)

    assert await _stored_total(async_session, user_id) == 3
    assert down_limiter.local.all_written()
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
        await flush_usage(down_limiter, broken_factory)
    assert down_limiter.local.unwritten[1].total() == 10

    with pytest.raises(RedisConnectionError):
        await flush_usage(down_limiter, session_factory)
    assert await _stored_total(async_session, user_id) == 10


async def _counters(redis, user_id, now: float) -> list[int | None]:
    return [
        None if value is None else int(value)
        for value in await redis.mget(Windows.at(now).counter_keys(user_id))
    ]


@pytest.mark.asyncio
async def test_a_write_that_failed_after_committing_is_not_repeated(
    auth_client, down_limiter, async_session, session_factory, make_user, make_key
):
    user_id = await make_user("free")
    key = await make_key(user_id)
    await _calls(auth_client, key, 3)

    real_write = usage_flush.write_usage

    async def lost_reply(*args, **kwargs):
        await real_write(*args, **kwargs)
        raise OSError("connection lost after the commit")

    with (
        patch.object(usage_flush, "write_usage", lost_reply),
        pytest.raises(OSError),
    ):
        await flush_usage(down_limiter, session_factory)
    await _calls(auth_client, key, 2)

    with pytest.raises(RedisConnectionError):
        await flush_usage(down_limiter, session_factory)

    assert await _stored_total(async_session, user_id) == 5
    assert down_limiter.local.all_written()


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

    assert await flush_usage(limiter, session_factory) == 6
    assert await _stored_total(async_session, user_id) == 6
    # The outage minute has ended; the hour and month gained the 3 outage calls
    assert await _counters(redis, user_id, clock.now) == [1, 6, 6]
    assert limiter.local.users == set()

    assert await _calls(auth_client, key, 1) == [200]
    assert await _counters(redis, user_id, clock.now) == [2, 7, 7]


@pytest.mark.asyncio
async def test_outage_calls_count_in_the_minute_they_were_made(
    auth_client, limiter, break_redis, redis, session_factory, make_user, make_key
):
    key = await make_key(await make_user("free"))
    break_redis.broken = False
    assert await _calls(auth_client, key, 4) == [200] * 4  # In Redis
    break_redis.broken = True
    assert await _calls(auth_client, key, 5) == [200] * 5  # In memory

    # Redis answers again within the same minute
    break_redis.broken = False
    limiter._down_until = 0
    assert await _calls(auth_client, key, 1) == [200]  # In Redis
    await flush_usage(limiter, session_factory)

    # The free tier allows 10 a minute: all used
    assert await _calls(auth_client, key, 1) == [429]


@pytest.mark.asyncio
async def test_counters_redis_lost_are_rebuilt_with_the_unflushed_outage_calls(
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
    assert await _calls(auth_client, key, 3) == [200] * 3  # In memory
    await redis.flushdb()  # Redis restarted empty

    break_redis.broken = False
    clock.now += BACKOFF_SECONDS
    # Nothing is in Postgres yet: the rebuild uses the in-memory counts
    assert await _calls(auth_client, key, 1) == [200]
    assert await _counters(redis, user_id, clock.now) == [1, 4, 4]
    assert limiter.local.users == set()

    await flush_usage(limiter, session_factory)
    assert await _stored_total(async_session, user_id) == 4
    assert await _counters(redis, user_id, clock.now) == [1, 4, 4]


@pytest.mark.asyncio
@pytest.mark.parametrize("rebuild_first", [True, False])
async def test_a_rebuild_and_the_reconcile_add_the_outage_calls_once(
    auth_client,
    limiter,
    break_redis,
    redis,
    session_factory,
    make_user,
    make_key,
    clock,
    rebuild_first,
):
    user_id = await make_user("free")
    key = await make_key(user_id)
    assert await _calls(auth_client, key, 3) == [200] * 3  # In memory
    # Writes the outage calls; Redis still counts as down, so no reconcile yet
    await flush_usage(limiter, session_factory)
    assert limiter.local.users == {user_id}

    principal = SimpleNamespace(user_id=user_id)
    windows = Windows.at(clock.now)
    rebuild = limiter._rebuild(principal, windows, clock.now)
    steps = [rebuild, limiter.reconcile()]
    for step in steps if rebuild_first else reversed(steps):
        await step

    assert (await _counters(redis, user_id, clock.now))[1:] == [3, 3]


@pytest.mark.asyncio
async def test_calls_counted_in_memory_during_the_reconcile_are_kept(
    auth_client,
    limiter,
    break_redis,
    redis,
    session_factory,
    make_user,
    make_key,
    clock,
):
    user_id = await make_user("free")
    key = await make_key(user_id)
    break_redis.broken = False
    await _calls(auth_client, key, 1)  # In Redis
    break_redis.broken = True
    await _calls(auth_client, key, 2)  # In memory
    break_redis.broken = False
    clock.now += BACKOFF_SECONDS
    await _calls(auth_client, key, 1)

    # Redis flaps while the flush adds the outage calls: one more call in memory
    real_add = limiter._add_script

    async def add_during_a_flap(**kwargs):
        windows = Windows.at(clock.now)
        principal = SimpleNamespace(
            user_id=user_id,
            key_id=uuid.uuid4(),
            per_minute=None,
            per_hour=None,
            per_month=None,
        )
        limiter.local.hit(principal, windows, clock.now)
        return await real_add(**kwargs)

    with patch.object(limiter, "_add_script", side_effect=add_during_a_flap):
        await flush_usage(limiter, session_factory)
    assert (await _counters(redis, user_id, clock.now))[2] == 4
    # Not sent yet, so not forgotten
    assert limiter.local.users == {user_id}
    assert limiter.local.unsent(user_id, Windows.at(clock.now)) == [1, 1, 1]

    # The next flush writes it to Postgres, then adds it
    await flush_usage(limiter, session_factory)
    assert (await _counters(redis, user_id, clock.now))[2] == 5
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
    break_redis.broken = False
    await _calls(auth_client, key, 1)  # In Redis
    break_redis.broken = True
    await _calls(auth_client, key, 2)  # In memory
    break_redis.broken = False
    clock.now += BACKOFF_SECONDS
    await _calls(auth_client, key, 1)

    real_write = usage_flush.write_usage
    with patch.object(
        usage_flush,
        "write_usage",
        AsyncMock(side_effect=[True, OSError("db down")]),
    ):
        with pytest.raises(OSError):
            await flush_usage(limiter, session_factory)
    # Not added before the outage calls are in Postgres
    assert (await _counters(redis, user_id, clock.now))[2] == 2
    assert limiter.local.users == {user_id}

    with patch.object(usage_flush, "write_usage", real_write):
        await flush_usage(limiter, session_factory)
    assert (await _counters(redis, user_id, clock.now))[2] == 4
    assert await _stored_total(async_session, user_id) == 2


@pytest.mark.asyncio
async def test_the_flusher_wakes_when_redis_comes_back():
    limiter = SimpleNamespace(recovered=asyncio.Event())
    flush = AsyncMock(return_value=0)
    with patch.object(usage_flush, "flush_usage", flush):
        task = asyncio.create_task(usage_flush.run_usage_flusher(limiter, None, 3600))
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
