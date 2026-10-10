"""The dev script that resets rate limit counters. Needs TEST_REDIS_URL."""

from unittest.mock import patch

import pytest

from geonames_server.api.rate_limit import Windows
from geonames_server.scripts import reset_usage
from geonames_server.scripts.reset_usage import reset_counters

PATH = "/metadata/"


async def _calls(client, key, n):
    return [
        (await client.get(PATH, headers={"X-API-Key": key})).status_code
        for _ in range(n)
    ]


async def _counters(redis, user_id, now):
    return [
        None if v is None else int(v)
        for v in await redis.mget(Windows.at(now).counter_keys(user_id))
    ]


@pytest.mark.asyncio
async def test_a_reset_lifts_a_limit_that_was_reached(
    auth_client, limiter, redis, clock, make_user, make_key
):
    user_id = await make_user("free")
    key = await make_key(user_id)
    assert await _calls(auth_client, key, 11) == [200] * 10 + [429]

    before = await reset_counters(redis, user_id, ["minute"], clock.now)

    assert before == {"minute": 10}
    assert await _calls(auth_client, key, 1) == [200]
    assert await _counters(redis, user_id, clock.now) == [1, 11, 11]


@pytest.mark.asyncio
async def test_only_the_chosen_windows_are_reset(
    auth_client, limiter, redis, clock, make_user, make_key
):
    user_id = await make_user("free")
    key = await make_key(user_id)
    await _calls(auth_client, key, 3)

    await reset_counters(redis, user_id, ["hour"], clock.now)

    assert await _counters(redis, user_id, clock.now) == [3, 0, 3]


@pytest.mark.asyncio
async def test_a_month_reset_is_not_undone_by_a_rebuild(
    auth_client, limiter, redis, clock, make_user, make_key, session_factory
):
    # The month counter is gone (Redis restarted) and Postgres has the usage:
    # the reset must stop the rebuild from bringing the month back.
    user_id = await make_user("free")
    key = await make_key(user_id)
    windows = Windows.at(clock.now)
    month_key = windows.counter_keys(user_id)[2]
    await _calls(auth_client, key, 1)
    await redis.set(month_key, 5000, keepttl=True)
    assert await _calls(auth_client, key, 1) == [429]

    await reset_counters(redis, user_id, ["hour", "month"], clock.now)

    assert await _calls(auth_client, key, 1) == [200]
    assert (await _counters(redis, user_id, clock.now))[1:] == [1, 1]
    assert await redis.ttl(month_key) > 0


def test_refuses_outside_the_devcontainer():
    with (
        patch.object(reset_usage.settings, "dev_seed_users", False),
        pytest.raises(SystemExit, match="DEV_SEED_USERS is not true"),
    ):
        reset_usage.main([])


def test_an_unknown_email_is_refused():
    with pytest.raises(SystemExit, match="No profile for nobody@example.invalid"):
        reset_usage.user_ids(["nobody@example.invalid"])
