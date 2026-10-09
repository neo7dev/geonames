"""API key lookups are cached for AUTH_CACHE_SECONDS; a portal revoke applies at once."""

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import func, text, update

from geonames_server.api import auth
from geonames_server.database.models.accounts import ApiKey, Profile, Tier


@pytest.fixture
def clock():
    """The cache's clock, moved by hand: clock.now += 61."""

    class Clock:
        now = 1000.0

    with patch.object(auth, "_monotonic", lambda: Clock.now):
        yield Clock


def _headers(key: str) -> dict:
    return {"X-API-Key": key}


async def _sync_status(client, key: str) -> int:
    """403 for non-admins, 404 (unknown sync) for admins."""
    resp = await client.get(f"/sync/{uuid.uuid4()}", headers=_headers(key))
    return resp.status_code


@pytest.mark.asyncio
async def test_a_cached_key_does_not_query_the_database(
    auth_client, async_session, make_user, make_key, clock
):
    key = await make_key(await make_user())
    assert (
        await auth_client.get("/metadata/", headers=_headers(key))
    ).status_code == 200

    with patch.object(async_session, "execute", wraps=async_session.execute) as spy:
        resp = await auth_client.get(
            "/sync/" + str(uuid.uuid4()), headers=_headers(key)
        )

    assert resp.status_code == 403
    spy.assert_not_called()


@pytest.mark.asyncio
async def test_a_role_change_applies_when_the_entry_expires(
    auth_client, async_session, make_user, make_key, clock
):
    user_id = await make_user()
    key = await make_key(user_id)
    assert await _sync_status(auth_client, key) == 403

    await async_session.execute(
        update(Profile).where(Profile.id == user_id).values(role="admin")
    )
    await async_session.commit()
    clock.now += 59
    assert await _sync_status(auth_client, key) == 403

    clock.now += 2
    assert await _sync_status(auth_client, key) == 404


@pytest.mark.asyncio
async def test_a_tier_limit_change_applies_when_the_entry_expires(
    auth_client, async_session, make_user, make_key, clock
):
    key = await make_key(await make_user())
    await auth_client.get("/metadata/", headers=_headers(key))

    await async_session.execute(
        update(Tier).where(Tier.name == "basic").values(per_minute=7)
    )
    await async_session.commit()
    await auth_client.get("/metadata/", headers=_headers(key))
    assert auth._cache_get(auth.hash_api_key(key)).per_minute == 10

    clock.now += 61
    await auth_client.get("/metadata/", headers=_headers(key))
    assert auth._cache_get(auth.hash_api_key(key)).per_minute == 7


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        pytest.param("revoke", id="studio-revoke"),
        pytest.param("ban", id="ban"),
    ],
)
async def test_studio_changes_apply_when_the_entry_expires(
    auth_client, async_session, make_user, make_key, clock, change
):
    user_id = await make_user()
    key = await make_key(user_id)
    assert (
        await auth_client.get("/metadata/", headers=_headers(key))
    ).status_code == 200

    if change == "revoke":
        await async_session.execute(
            update(ApiKey)
            .where(ApiKey.user_id == user_id)
            .values(revoked_at=func.now())
        )
    else:
        await async_session.execute(
            text(
                "UPDATE auth.users SET banned_until = now() + interval '1 day' "
                "WHERE id = :id"
            ),
            {"id": user_id},
        )
    await async_session.commit()

    assert (
        await auth_client.get("/metadata/", headers=_headers(key))
    ).status_code == 200
    clock.now += 61
    assert (
        await auth_client.get("/metadata/", headers=_headers(key))
    ).status_code == 401


@pytest.mark.asyncio
async def test_a_portal_revoke_applies_on_the_next_request(
    auth_client, make_user, make_key, make_jwt, clock
):
    user_id = await make_user()
    key = await make_key(user_id)
    assert (
        await auth_client.get("/metadata/", headers=_headers(key))
    ).status_code == 200
    login = {"Authorization": f"Bearer {make_jwt(user_id)}"}
    (listed,) = (await auth_client.get("/portal/keys", headers=login)).json()

    resp = await auth_client.delete(f"/portal/keys/{listed['id']}", headers=login)

    assert resp.status_code == 204
    assert (
        await auth_client.get("/metadata/", headers=_headers(key))
    ).status_code == 401


@pytest.mark.asyncio
async def test_unknown_keys_are_not_cached(auth_client, clock):
    await auth_client.get("/metadata/", headers=_headers("gn_unknown"))

    assert auth._principal_cache == {}


@pytest.mark.asyncio
async def test_zero_seconds_turns_the_cache_off(
    auth_client, make_user, make_key, clock
):
    key = await make_key(await make_user())

    with patch.object(auth.settings, "auth_cache_seconds", 0):
        await auth_client.get("/metadata/", headers=_headers(key))

    assert auth._principal_cache == {}


def _principal(n: int) -> auth.Principal:
    return auth.Principal(
        user_id=uuid.UUID(int=n),
        email=None,
        key_id=uuid.UUID(int=n),
        role="free",
        is_admin=False,
        tier="basic",
        per_minute=10,
        per_hour=100,
        per_month=5000,
        max_keys=1,
    )


def test_a_full_cache_evicts_the_oldest(clock):
    with patch.object(auth, "_MAX_CACHED_KEYS", 3):
        for n in range(4):
            auth._cache_put(f"hash{n}", _principal(n))

    assert list(auth._principal_cache) == ["hash1", "hash2", "hash3"]


def test_expired_entries_are_dropped_before_live_ones(clock):
    auth._cache_put("old", _principal(1))
    clock.now += 61
    auth._cache_put("new", _principal(2))

    assert list(auth._principal_cache) == ["new"]


def test_forget_api_key_drops_only_that_key(clock):
    auth._cache_put("a", _principal(1))
    auth._cache_put("b", _principal(2))

    auth.forget_api_key(uuid.UUID(int=1))

    assert list(auth._principal_cache) == ["b"]
