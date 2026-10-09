"""Per-user limits per minute, hour and month, counted in Redis.

Needs TEST_REDIS_URL (a spare Redis database; it is flushed) and the Supabase
database the other API tests use.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select, update

from geonames_server.api import auth, dependencies, rate_limit
from geonames_server.api.rate_limit import USAGE_PENDING, Windows
from geonames_server.database.models.accounts import ApiKey, ApiKeyUsage, Profile

PATH = "/metadata/"


def _headers(key: str) -> dict:
    return {"X-API-Key": key}


async def _calls(client, key: str, n: int, path: str = PATH) -> list[int]:
    return [
        (await client.get(path, headers=_headers(key))).status_code for _ in range(n)
    ]


async def _counter(redis, user_id, clock, window: int) -> int:
    value = await redis.get(Windows.at(clock.now).counter_keys(user_id)[window])
    return int(value or 0)


async def _preset(redis, user_id, clock, window: int, value: int) -> None:
    await redis.set(Windows.at(clock.now).counter_keys(user_id)[window], value)


@pytest.mark.asyncio
async def test_the_11th_call_in_a_minute_on_basic_gets_429(
    auth_client, limiter, make_user, make_key, clock
):
    key = await make_key(await make_user("free"))

    assert await _calls(auth_client, key, 10) == [200] * 10
    resp = await auth_client.get(PATH, headers=_headers(key))

    assert resp.status_code == 429
    # The clock is 30 s into the minute
    assert resp.headers["Retry-After"] == "30"
    assert resp.json()["detail"] == (
        "Rate limit exceeded: 10 requests per minute on the basic tier"
    )

    clock.now += 30
    assert await _calls(auth_client, key, 1) == [200]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "window, value, retry_after, detail",
    [
        pytest.param(1, 100, 3570, "100 requests per hour", id="hour"),
        # 2026-10-09 12:00:30 to 2026-11-01 00:00:00
        pytest.param(
            2,
            5000,
            22 * 86400 + 11 * 3600 + 3570,
            "5000 requests per month",
            id="month",
        ),
    ],
)
async def test_hour_and_month_limits(
    auth_client,
    limiter,
    redis,
    make_user,
    make_key,
    clock,
    window,
    value,
    retry_after,
    detail,
):
    user_id = await make_user("free")
    key = await make_key(user_id)
    await _preset(redis, user_id, clock, window, value)

    resp = await auth_client.get(PATH, headers=_headers(key))

    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == str(retry_after)
    assert detail in resp.json()["detail"]


@pytest.mark.asyncio
async def test_the_longest_wait_is_reported(
    auth_client, limiter, redis, make_user, make_key, clock
):
    user_id = await make_user("free")
    key = await make_key(user_id)
    await _preset(redis, user_id, clock, 0, 10)
    await _preset(redis, user_id, clock, 2, 5000)

    resp = await auth_client.get(PATH, headers=_headers(key))

    assert "per month" in resp.json()["detail"]
    assert int(resp.headers["Retry-After"]) > 3600


@pytest.mark.asyncio
async def test_rejected_calls_do_not_count(
    auth_client, limiter, redis, make_user, make_key, clock
):
    user_id = await make_user("free")
    key = await make_key(user_id)

    await _calls(auth_client, key, 15)

    assert [await _counter(redis, user_id, clock, w) for w in range(3)] == [10, 10, 10]


@pytest.mark.asyncio
async def test_a_users_keys_share_one_limit(
    auth_client, limiter, redis, make_user, make_key, clock
):
    user_id = await make_user("tester")  # plus: 30 a minute
    first, second = await make_key(user_id, "a"), await make_key(user_id, "b")
    await _preset(redis, user_id, clock, 0, 29)

    assert await _calls(auth_client, first, 1) == [200]
    assert await _calls(auth_client, second, 1) == [429]


@pytest.mark.asyncio
async def test_users_do_not_affect_each_other(
    auth_client, limiter, make_user, make_key, clock
):
    limited = await make_key(await make_user("free"))
    other = await make_key(await make_user("free"))

    await _calls(auth_client, limited, 11)

    assert await _calls(auth_client, other, 1) == [200]


@pytest.mark.asyncio
async def test_unlimited_tiers_are_counted_but_never_limited(
    auth_client, limiter, redis, make_user, make_key, clock
):
    user_id = await make_user("sr_dev")  # max
    key = await make_key(user_id)
    await _preset(redis, user_id, clock, 0, 1_000_000)

    assert await _calls(auth_client, key, 1) == [200]
    assert await _counter(redis, user_id, clock, 0) == 1_000_001


@pytest.mark.asyncio
async def test_each_call_is_recorded_for_the_usage_flush(
    auth_client, limiter, redis, make_user, make_key, clock, async_session
):
    user_id = await make_user("free")
    key = await make_key(user_id)
    await _calls(auth_client, key, 3)

    key_id = auth._cache_get(auth.hash_api_key(key)).key_id
    hour = Windows.at(clock.now).hour
    assert await redis.hgetall(USAGE_PENDING) == {
        f"n:{key_id}:{hour}".encode(): b"3",
        f"t:{key_id}".encode(): str(int(clock.now)).encode(),
    }


@pytest.mark.asyncio
async def test_sync_counts_but_a_refused_non_admin_does_not(
    auth_client, limiter, redis, make_user, make_key, clock
):
    admin_id = await make_user("admin")
    free_id = await make_user("free")
    sync_status = f"/sync/{uuid.uuid4()}"

    assert await _calls(auth_client, await make_key(admin_id), 1, sync_status) == [404]
    assert await _calls(auth_client, await make_key(free_id), 1, sync_status) == [403]

    assert await _counter(redis, admin_id, clock, 0) == 1
    assert await _counter(redis, free_id, clock, 0) == 0


@pytest.mark.asyncio
async def test_portal_health_and_unknown_keys_are_not_counted(
    auth_client, limiter, redis, make_user, make_jwt, clock
):
    user_id = await make_user("free")

    me = await auth_client.get(
        "/portal/me", headers={"Authorization": f"Bearer {make_jwt(user_id)}"}
    )
    health = await auth_client.get("/health")
    unknown = await auth_client.get(PATH, headers=_headers("gn_unknown"))

    assert (me.status_code, health.status_code, unknown.status_code) == (200, 200, 401)
    assert await redis.dbsize() == 0


@pytest.mark.asyncio
async def test_a_limited_user_gets_429_before_a_building_datasets_503(
    auth_client, limiter, redis, make_user, make_key, clock
):
    user_id = await make_user("free")
    key = await make_key(user_id)
    await _preset(redis, user_id, clock, 0, 10)
    dependencies._built.discard("cities15000")

    with (
        patch.object(dependencies.settings, "enabled_datasets", ["cities15000"]),
        patch(f"{dependencies.__name__}._is_built", AsyncMock(return_value=False)),
    ):
        assert await _calls(auth_client, key, 1, "/cities15000/") == [429]


@pytest.mark.asyncio
async def test_without_redis_requests_are_not_limited(auth_client, make_user, make_key):
    key = await make_key(await make_user("free"))

    assert await _calls(auth_client, key, 11) == [200] * 11


@pytest.mark.asyncio
async def test_a_role_change_lifts_the_limit_within_the_cache_time(
    auth_client, limiter, async_session, make_user, make_key, clock
):
    """Phase 5's done-when check: a role change in Studio applies within 60 s."""
    user_id = await make_user("free")
    key = await make_key(user_id)
    cache_time = [1000.0]
    with patch.object(auth, "_monotonic", lambda: cache_time[0]):
        assert await _calls(auth_client, key, 11) == [200] * 10 + [429]

        await async_session.execute(
            update(Profile).where(Profile.id == user_id).values(role="dev")
        )
        await async_session.commit()
        assert await _calls(auth_client, key, 1) == [429]

        cache_time[0] += 61
        assert await _calls(auth_client, key, 1) == [200]


async def _stored(async_session, user_id, *usage: tuple[datetime, int]) -> None:
    """Flushed usage rows for the user's (first) key."""
    key_id = await async_session.scalar(
        select(ApiKey.id).where(ApiKey.user_id == user_id)
    )
    async_session.add_all(
        ApiKeyUsage(key_id=key_id, hour=hour, requests=n) for hour, n in usage
    )
    await async_session.commit()


def _hour_of(now: float, hours_later: int = 0) -> datetime:
    return datetime.fromtimestamp((int(now) // 3600 + hours_later) * 3600, timezone.utc)


class TestRebuildFromPostgres:
    @pytest.mark.asyncio
    async def test_the_month_count_is_rebuilt(
        self, auth_client, limiter, async_session, make_user, make_key, clock
    ):
        user_id = await make_user("free")
        key = await make_key(user_id)
        # Earlier this month, in two different hours
        await _stored(
            async_session,
            user_id,
            (_hour_of(clock.now, -48), 4000),
            (_hour_of(clock.now, -2), 995),
        )

        statuses = await _calls(auth_client, key, 6)

        assert statuses == [200] * 5 + [429]
        resp = await auth_client.get(PATH, headers=_headers(key))
        assert "per month" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_the_hour_count_is_rebuilt(
        self, auth_client, limiter, async_session, make_user, make_key, clock
    ):
        user_id = await make_user("free")
        key = await make_key(user_id)
        await _stored(async_session, user_id, (_hour_of(clock.now), 100))

        resp = await auth_client.get(PATH, headers=_headers(key))

        assert resp.status_code == 429
        assert "per hour" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_revoked_keys_count(
        self, auth_client, limiter, async_session, make_user, make_key, clock
    ):
        user_id = await make_user("tester")  # plus: 500 an hour
        await make_key(user_id, "old")
        await _stored(async_session, user_id, (_hour_of(clock.now), 500))
        await async_session.execute(
            update(ApiKey)
            .where(ApiKey.user_id == user_id)
            .values(revoked_at=func.now())
        )
        await async_session.commit()
        key = await make_key(user_id, "new")

        assert await _calls(auth_client, key, 1) == [429]

    @pytest.mark.asyncio
    async def test_a_new_month_starts_at_zero(
        self, auth_client, limiter, redis, async_session, make_user, make_key, clock
    ):
        user_id = await make_user("free")
        key = await make_key(user_id)
        # Last month's usage only
        await _stored(
            async_session,
            user_id,
            (datetime(2026, 9, 30, 23, tzinfo=timezone.utc), 5000),
        )

        assert await _calls(auth_client, key, 1) == [200]
        assert await _counter(redis, user_id, clock, 2) == 1

    @pytest.mark.asyncio
    async def test_counters_in_redis_are_not_overwritten(
        self, auth_client, limiter, redis, async_session, make_user, make_key, clock
    ):
        user_id = await make_user("free")
        key = await make_key(user_id)
        await _stored(async_session, user_id, (_hour_of(clock.now, -2), 4999))
        await _preset(redis, user_id, clock, 2, 3)

        assert await _calls(auth_client, key, 1) == [200]
        assert await _counter(redis, user_id, clock, 2) == 4

    @pytest.mark.asyncio
    async def test_a_failed_rebuild_lets_the_request_through(
        self, auth_client, limiter, redis, make_user, make_key, clock
    ):
        key = await make_key(await make_user("free"))

        with (
            patch.object(
                rate_limit, "stored_usage", AsyncMock(side_effect=OSError("db down"))
            ),
            patch.object(rate_limit.log, "warning") as warning,
        ):
            assert await _calls(auth_client, key, 1) == [200]

        warning.assert_called_once()
        # Nothing was counted, so the next request rebuilds
        assert await redis.dbsize() == 0

    @pytest.mark.asyncio
    async def test_a_counter_missing_twice_lets_the_request_through(
        self, auth_client, limiter, make_user, make_key, clock
    ):
        key = await make_key(await make_user("free"))

        with patch.object(limiter, "_rebuild", AsyncMock(return_value=True)):
            assert await _calls(auth_client, key, 1) == [200]
