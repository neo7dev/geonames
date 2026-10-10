"""/portal: login token checks, profile and limits, API key management."""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError

from geonames_server.api.app import app
from geonames_server.api.rate_limit import RateLimiter
from geonames_server.config.config import settings
from geonames_server.database.models.accounts import ApiKey, ApiKeyUsage, Profile, Role


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def login(make_user, make_jwt):
    """Sign up a user with *role* and return (user id, auth headers)."""

    async def _login(role: str = "free", email: str | None = None):
        user_id = await make_user(role, email)
        return user_id, _bearer(make_jwt(user_id))

    return _login


async def _create_key(client, headers, name="my key"):
    return await client.post("/portal/keys", json={"name": name}, headers=headers)


class TestLoginToken:
    @pytest.mark.asyncio
    async def test_missing_token(self, auth_client):
        resp = await auth_client.get("/portal/me")

        assert resp.status_code == 401
        assert resp.headers["WWW-Authenticate"] == "Bearer"

    @pytest.mark.asyncio
    async def test_login_without_jwt_secret_is_unavailable(
        self, auth_client, make_user, make_jwt
    ):
        token = make_jwt(await make_user("free"))

        with patch.object(settings, "jwt_secret", None):
            resp = await auth_client.get(
                "/portal/me", headers={"Authorization": f"Bearer {token}"}
            )

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Login is not configured on this server"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "claims",
        [
            pytest.param({"expires_in": -timedelta(minutes=5)}, id="expired"),
            pytest.param({"secret": "not-the-secret-" * 4}, id="wrong-secret"),
            pytest.param({"aud": "someone-else"}, id="wrong-audience"),
            pytest.param({"aud": None}, id="no-audience"),
            pytest.param({"exp": None}, id="no-expiry"),
            pytest.param({"role": "anon"}, id="anon-role"),
            pytest.param({"role": "service_role"}, id="service-role"),
            pytest.param({"is_anonymous": True}, id="anonymous-user"),
        ],
    )
    async def test_invalid_token(self, auth_client, make_user, make_jwt, claims):
        user_id = await make_user()
        token = make_jwt(user_id, **claims)

        resp = await auth_client.get("/portal/me", headers=_bearer(token))

        assert resp.status_code == 401
        assert resp.headers["WWW-Authenticate"] == "Bearer"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("role", ["anon", "service_role"])
    async def test_supabase_api_keys_are_rejected(self, auth_client, make_jwt, role):
        # Shaped like ANON_KEY / SERVICE_ROLE_KEY: same secret, no sub or aud.
        token = make_jwt(None, aud=None, is_anonymous=None, role=role, iss="supabase")

        resp = await auth_client.get("/portal/me", headers=_bearer(token))

        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_subject_must_be_a_user_id(self, auth_client, make_jwt):
        resp = await auth_client.get(
            "/portal/me", headers=_bearer(make_jwt("not-a-uuid"))
        )

        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_api_key_is_not_accepted_as_login_token(
        self, auth_client, make_user, make_key
    ):
        key = await make_key(await make_user())

        resp = await auth_client.get("/portal/me", headers=_bearer(key))

        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_user_without_profile_is_forbidden(self, auth_client, make_jwt):
        resp = await auth_client.get(
            "/portal/me", headers=_bearer(make_jwt(uuid.uuid4()))
        )

        assert resp.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "change", ["banned_until = now() + interval '1 hour'", "deleted_at = now()"]
    )
    async def test_banned_or_soft_deleted_user_is_forbidden(
        self, auth_client, async_session, login, change
    ):
        user_id, headers = await login()
        await async_session.execute(
            text(f"UPDATE auth.users SET {change} WHERE id = :id"), {"id": user_id}
        )
        await async_session.commit()

        resp = await auth_client.get("/portal/me", headers=headers)

        assert resp.status_code == 403
        assert "suspended" in resp.json()["detail"]


class TestMe:
    @pytest.mark.asyncio
    async def test_new_signup_is_free_on_basic(self, auth_client, login):
        user_id, headers = await login(email="new@example.com")

        resp = await auth_client.get("/portal/me", headers=headers)

        assert resp.status_code == 200
        assert resp.json() == {
            "id": str(user_id),
            "email": "new@example.com",
            "role": "free",
            "tier": "basic",
            "limits": {
                "per_minute": 10,
                "per_hour": 100,
                "per_month": 5000,
                "max_keys": 1,
            },
            "active_keys": 0,
        }

    @pytest.mark.asyncio
    async def test_renamed_default_role_still_applies_to_signups(
        self, auth_client, async_session, login
    ):
        await async_session.execute(
            update(Role).where(Role.name == "free").values(name="basic_user")
        )
        await async_session.commit()

        _, headers = await login()

        assert (await auth_client.get("/portal/me", headers=headers)).json()[
            "role"
        ] == ("basic_user")

    @pytest.mark.asyncio
    async def test_signups_get_whichever_role_is_default(
        self, auth_client, async_session, login
    ):
        await async_session.execute(
            update(Role).values(is_default=Role.name == "tester")
        )
        await async_session.commit()

        _, headers = await login()

        body = (await auth_client.get("/portal/me", headers=headers)).json()
        assert (body["role"], body["tier"]) == ("tester", "plus")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "change",
        [
            pytest.param(update(Role).values(is_default=False), id="no-default"),
            pytest.param(update(Role).values(is_default=True), id="two-defaults"),
            pytest.param(
                delete(Role).where(Role.name == "test_default"), id="delete-default"
            ),
        ],
    )
    async def test_exactly_one_role_stays_default(self, async_session, change):
        # A default role no profile uses, so deleting it touches no real users
        async_session.add(Role(name="test_default", tier="basic"))
        await async_session.execute(
            update(Role).values(is_default=Role.name == "test_default")
        )
        # Checked now, so the setup passes and the change below is checked as it runs
        await async_session.execute(
            text("SET CONSTRAINTS app.roles_one_default IMMEDIATE")
        )

        with pytest.raises(DBAPIError, match="exactly one default role"):
            await async_session.execute(change)

    @pytest.mark.asyncio
    async def test_max_tier_is_unlimited(self, auth_client, login):
        _, headers = await login("sr_dev")

        body = (await auth_client.get("/portal/me", headers=headers)).json()

        assert body["tier"] == "max"
        assert body["limits"] == {
            "per_minute": None,
            "per_hour": None,
            "per_month": None,
            "max_keys": None,
        }

    @pytest.mark.asyncio
    async def test_role_change_applies_on_next_request(
        self, auth_client, async_session, login
    ):
        user_id, headers = await login()
        await async_session.execute(
            update(Profile).where(Profile.id == user_id).values(role="dev")
        )
        await async_session.commit()

        body = (await auth_client.get("/portal/me", headers=headers)).json()

        assert (body["role"], body["tier"]) == ("dev", "pro")

    @pytest.mark.asyncio
    async def test_email_change_reaches_the_profile(
        self, auth_client, async_session, login
    ):
        user_id, headers = await login(email="old@example.com")
        await async_session.execute(
            text("UPDATE auth.users SET email = 'new@example.com' WHERE id = :id"),
            {"id": user_id},
        )
        await async_session.commit()

        body = (await auth_client.get("/portal/me", headers=headers)).json()

        assert body["email"] == "new@example.com"


class TestKeys:
    @pytest.mark.asyncio
    async def test_create_returns_the_key_once(self, auth_client, async_session, login):
        user_id, headers = await login()

        created = await _create_key(auth_client, headers, name="  laptop  ")
        listed = await auth_client.get("/portal/keys", headers=headers)

        assert created.status_code == 201
        body = created.json()
        key = body["key"]
        assert key.startswith("gn_")
        assert body["name"] == "laptop"
        assert body["prefix"] == key[:11]
        assert body["last_used_at"] is None

        assert listed.status_code == 200
        assert listed.json() == [{k: v for k, v in body.items() if k != "key"}]

        stored = await async_session.scalar(
            select(ApiKey).where(ApiKey.user_id == user_id)
        )
        assert stored.key_hash == hashlib.sha256(key.encode()).hexdigest()

    @pytest.mark.asyncio
    async def test_created_key_works_on_data_routes(self, auth_client, login):
        _, headers = await login()
        key = (await _create_key(auth_client, headers)).json()["key"]

        resp = await auth_client.get("/deletions/", headers={"X-API-Key": key})

        assert resp.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", ["", "   ", "x" * 65])
    async def test_name_must_be_1_to_64_characters(self, auth_client, login, name):
        _, headers = await login()

        resp = await _create_key(auth_client, headers, name=name)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", ["a\x00b", "line\nbreak", "tab\there", "del\x7f"])
    async def test_name_rejects_control_characters(self, auth_client, login, name):
        _, headers = await login()

        resp = await _create_key(auth_client, headers, name=name)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_name_keeps_unicode(self, auth_client, login):
        _, headers = await login()

        resp = await _create_key(auth_client, headers, name="Clé de test 🔑")

        assert resp.status_code == 201
        assert resp.json()["name"] == "Clé de test 🔑"

    @pytest.mark.asyncio
    async def test_list_is_newest_first(self, auth_client, async_session, login):
        _, headers = await login("dev")
        first = (await _create_key(auth_client, headers, "first")).json()
        await _create_key(auth_client, headers, "second")
        # Tests run in one transaction, where now() does not move
        await async_session.execute(
            update(ApiKey)
            .where(ApiKey.id == uuid.UUID(first["id"]))
            .values(created_at=ApiKey.created_at - timedelta(minutes=1))
        )
        await async_session.commit()

        names = [
            k["name"]
            for k in (await auth_client.get("/portal/keys", headers=headers)).json()
        ]

        assert names == ["second", "first"]

    @pytest.mark.asyncio
    async def test_revoke(self, auth_client, login):
        _, headers = await login()
        created = (await _create_key(auth_client, headers)).json()

        revoked = await auth_client.delete(
            f"/portal/keys/{created['id']}", headers=headers
        )
        listed = await auth_client.get("/portal/keys", headers=headers)
        data = await auth_client.get(
            "/deletions/", headers={"X-API-Key": created["key"]}
        )
        again = await auth_client.delete(
            f"/portal/keys/{created['id']}", headers=headers
        )

        assert revoked.status_code == 204
        assert listed.json() == []
        assert data.status_code == 401
        assert again.status_code == 404

    @pytest.mark.asyncio
    async def test_cannot_revoke_another_users_key(self, auth_client, login):
        _, owner = await login()
        _, other = await login()
        created = (await _create_key(auth_client, owner)).json()

        resp = await auth_client.delete(f"/portal/keys/{created['id']}", headers=other)

        assert resp.status_code == 404
        data = await auth_client.get(
            "/deletions/", headers={"X-API-Key": created["key"]}
        )
        assert data.status_code == 200

    @pytest.mark.asyncio
    async def test_keys_of_other_users_are_not_listed(self, auth_client, login):
        _, owner = await login()
        _, other = await login()
        await _create_key(auth_client, owner)

        assert (await auth_client.get("/portal/keys", headers=other)).json() == []


async def _roll(client, headers, key_id):
    return await client.post(f"/portal/keys/{key_id}/roll", headers=headers)


class TestRollKey:
    @pytest.mark.asyncio
    async def test_replaces_the_key_under_the_same_name(self, auth_client, login):
        _, headers = await login()
        old = (await _create_key(auth_client, headers, "laptop")).json()
        # Used once, so the principal cache holds it
        used = await auth_client.get("/deletions/", headers={"X-API-Key": old["key"]})
        assert used.status_code == 200

        resp = await _roll(auth_client, headers, old["id"])

        assert resp.status_code == 201
        new = resp.json()
        assert new["name"] == "laptop"
        assert new["id"] != old["id"]
        assert new["key"] != old["key"]
        assert new["key"].startswith(new["prefix"])
        listed = (await auth_client.get("/portal/keys", headers=headers)).json()
        assert [k["id"] for k in listed] == [new["id"]]
        assert "key" not in listed[0]
        new_call = await auth_client.get(
            "/deletions/", headers={"X-API-Key": new["key"]}
        )
        old_call = await auth_client.get(
            "/deletions/", headers={"X-API-Key": old["key"]}
        )
        assert new_call.status_code == 200
        assert old_call.status_code == 401

    @pytest.mark.asyncio
    async def test_works_at_the_tiers_key_limit(self, auth_client, login):
        _, headers = await login("free")  # basic tier: 1 key
        old = (await _create_key(auth_client, headers)).json()

        assert (await _roll(auth_client, headers, old["id"])).status_code == 201

        me = (await auth_client.get("/portal/me", headers=headers)).json()
        assert me["active_keys"] == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("which", ["another users", "revoked", "unknown"])
    async def test_only_the_users_active_keys_roll(self, auth_client, login, which):
        _, owner = await login()
        _, other = await login()
        key = (await _create_key(auth_client, owner)).json()
        headers, key_id = owner, key["id"]
        if which == "another users":
            headers = other
        elif which == "revoked":
            await auth_client.delete(f"/portal/keys/{key_id}", headers=owner)
        else:
            key_id = str(uuid.uuid4())

        resp = await _roll(auth_client, headers, key_id)

        assert resp.status_code == 404
        for h in (owner, other):
            listed = (await auth_client.get("/portal/keys", headers=h)).json()
            assert [k["id"] for k in listed] == (
                [] if which == "revoked" or h is other else [key["id"]]
            )

    @pytest.mark.asyncio
    async def test_needs_a_login_token(self, auth_client):
        resp = await auth_client.post(f"/portal/keys/{uuid.uuid4()}/roll")

        assert resp.status_code == 401


class TestKeyLimits:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("role", "tier", "limit"),
        [("free", "basic", 1), ("tester", "plus", 3), ("dev", "pro", 5)],
    )
    async def test_tier_caps_active_keys(self, auth_client, login, role, tier, limit):
        _, headers = await login(role)
        for i in range(limit):
            assert (
                await _create_key(auth_client, headers, f"key {i}")
            ).status_code == 201

        resp = await _create_key(auth_client, headers, "one too many")

        assert resp.status_code == 409
        assert f"{tier} tier allows {limit}" in resp.json()["detail"]
        me = (await auth_client.get("/portal/me", headers=headers)).json()
        assert me["active_keys"] == limit

    @pytest.mark.asyncio
    @pytest.mark.parametrize("role", ["sr_dev", "admin"])
    async def test_max_tier_is_unlimited(self, auth_client, login, role):
        _, headers = await login(role)

        for i in range(8):
            assert (
                await _create_key(auth_client, headers, f"key {i}")
            ).status_code == 201

    @pytest.mark.asyncio
    async def test_revoked_keys_do_not_count(self, auth_client, login):
        _, headers = await login()
        first = (await _create_key(auth_client, headers)).json()
        await auth_client.delete(f"/portal/keys/{first['id']}", headers=headers)

        assert (await _create_key(auth_client, headers)).status_code == 201

    @pytest.mark.asyncio
    async def test_downgrade_keeps_existing_keys_but_blocks_new_ones(
        self, auth_client, async_session, login
    ):
        user_id, headers = await login("dev")
        keys = [
            (await _create_key(auth_client, headers, f"key {i}")).json()["key"]
            for i in range(5)
        ]
        await async_session.execute(
            update(Profile).where(Profile.id == user_id).values(role="free")
        )
        await async_session.commit()

        for key in keys:
            data = await auth_client.get("/deletions/", headers={"X-API-Key": key})
            assert data.status_code == 200
        assert (await _create_key(auth_client, headers)).status_code == 409


async def _usage(client, headers, **params):
    return await client.get("/portal/usage", headers=headers, params=params)


async def _flushed(async_session, *usage):
    """Flushed usage rows: (key id, hour, requests)."""
    async_session.add_all(
        ApiKeyUsage(key_id=key_id, hour=hour, requests=n) for key_id, hour, n in usage
    )
    await async_session.commit()


async def _key_id(async_session, user_id, name=None):
    query = select(ApiKey.id).where(ApiKey.user_id == user_id)
    if name is not None:
        query = query.where(ApiKey.name == name)
    return await async_session.scalar(query)


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


class TestUsage:
    @pytest.mark.asyncio
    async def test_live_counts_rise_with_each_call(
        self, auth_client, limiter, login, make_key, clock
    ):
        user_id, headers = await login("free")
        key = await make_key(user_id)
        for _ in range(3):
            await auth_client.get("/metadata/", headers={"X-API-Key": key})

        body = (await _usage(auth_client, headers)).json()

        assert body["tier"] == "basic"
        assert body["live"] is True
        assert body["current"] == {
            "minute": {"used": 3, "limit": 10, "resets_at": "2026-10-09T12:01:00Z"},
            "hour": {"used": 3, "limit": 100, "resets_at": "2026-10-09T13:00:00Z"},
            "month": {"used": 3, "limit": 5000, "resets_at": "2026-11-01T00:00:00Z"},
        }

        await auth_client.get("/metadata/", headers={"X-API-Key": key})
        body = (await _usage(auth_client, headers)).json()
        assert body["current"]["minute"]["used"] == 4

    @pytest.mark.asyncio
    async def test_unlimited_tiers_show_null_limits(
        self, auth_client, limiter, login, clock
    ):
        _, headers = await login("sr_dev")

        body = (await _usage(auth_client, headers)).json()

        assert body["tier"] == "max"
        assert [body["current"][w]["limit"] for w in ("minute", "hour", "month")] == [
            None,
            None,
            None,
        ]

    @pytest.mark.asyncio
    async def test_an_hour_without_calls_shows_zero(
        self, auth_client, limiter, login, make_key, clock
    ):
        user_id, headers = await login("free")
        key = await make_key(user_id)
        await auth_client.get("/metadata/", headers={"X-API-Key": key})
        clock.now += 3600

        body = (await _usage(auth_client, headers)).json()

        used = [body["current"][w]["used"] for w in ("minute", "hour", "month")]
        assert used == [0, 0, 1]

    @pytest.mark.asyncio
    async def test_before_the_counters_are_rebuilt_the_month_comes_from_postgres(
        self, auth_client, limiter, async_session, login, make_key, clock
    ):
        user_id, headers = await login("free")
        await make_key(user_id)
        key_id = await _key_id(async_session, user_id)
        await _flushed(
            async_session,
            (key_id, _utc(2026, 10, 9, 12), 4),
            (key_id, _utc(2026, 10, 1), 3),
        )

        body = (await _usage(auth_client, headers)).json()

        assert body["live"] is True
        used = [body["current"][w]["used"] for w in ("minute", "hour", "month")]
        assert used == [0, 4, 7]

    @pytest.mark.asyncio
    async def test_while_redis_is_down_the_in_memory_counts_show(
        self, auth_client, login, make_key, session_factory, clock
    ):
        down = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=1)
        app.state.rate_limiter = RateLimiter(down, session_factory)
        try:
            user_id, headers = await login("free")
            key = await make_key(user_id)
            for _ in range(2):
                await auth_client.get("/metadata/", headers={"X-API-Key": key})

            body = (await _usage(auth_client, headers)).json()
        finally:
            del app.state.rate_limiter
            await down.aclose()

        assert body["live"] is False
        used = [body["current"][w]["used"] for w in ("minute", "hour", "month")]
        assert used == [2, 2, 2]

    @pytest.mark.asyncio
    async def test_when_redis_fails_on_the_usage_read_the_flushed_usage_shows(
        self, auth_client, limiter, async_session, login, make_key, clock
    ):
        user_id, headers = await login("free")
        await make_key(user_id)
        await _flushed(
            async_session,
            (await _key_id(async_session, user_id), _utc(2026, 10, 9, 12), 5),
        )

        with patch.object(
            limiter.redis, "mget", side_effect=RedisConnectionError("Redis is down")
        ):
            body = (await _usage(auth_client, headers)).json()

        assert body["live"] is False
        used = [body["current"][w]["used"] for w in ("minute", "hour", "month")]
        assert used == [None, 5, 5]
        # Later reads skip Redis until the backoff ends
        assert limiter.outage

    @pytest.mark.asyncio
    async def test_without_redis_the_flushed_usage_shows(
        self, auth_client, async_session, login, make_key, clock
    ):
        user_id, headers = await login("free")
        await make_key(user_id)
        await _flushed(
            async_session,
            (await _key_id(async_session, user_id), _utc(2026, 10, 9, 12), 5),
        )

        body = (await _usage(auth_client, headers)).json()

        assert body["live"] is False
        used = [body["current"][w]["used"] for w in ("minute", "hour", "month")]
        assert used == [None, 5, 5]

    @pytest.mark.asyncio
    async def test_daily_history_is_zero_filled_utc_days(
        self, auth_client, async_session, login, make_key, clock
    ):
        user_id, headers = await login("tester")
        await make_key(user_id, "a")
        await make_key(user_id, "b")
        a = await _key_id(async_session, user_id, "a")
        b = await _key_id(async_session, user_id, "b")
        await _flushed(
            async_session,
            (a, _utc(2026, 10, 7, 23), 2),
            (b, _utc(2026, 10, 7, 1), 3),
            (a, _utc(2026, 10, 9, 0), 4),
            # Before the window
            (a, _utc(2026, 10, 6, 23), 100),
        )
        body = (await _usage(auth_client, headers, days=3)).json()

        assert body["daily"] == [
            {"date": "2026-10-07", "requests": 5},
            {"date": "2026-10-08", "requests": 0},
            {"date": "2026-10-09", "requests": 4},
        ]

    @pytest.mark.asyncio
    async def test_keys_show_this_months_requests_and_revoked_keys_used(
        self, auth_client, async_session, login, make_key, clock
    ):
        user_id, headers = await login("tester")
        for name in ("active", "revoked-used", "revoked-unused"):
            await make_key(user_id, name)
        ids = {
            name: await _key_id(async_session, user_id, name)
            for name in ("active", "revoked-used", "revoked-unused")
        }
        await _flushed(
            async_session,
            (ids["active"], _utc(2026, 10, 2), 6),
            (ids["active"], _utc(2026, 9, 30), 50),
            (ids["revoked-used"], _utc(2026, 10, 3), 2),
            (ids["revoked-unused"], _utc(2026, 9, 3), 9),
        )
        await async_session.execute(
            update(ApiKey)
            .where(ApiKey.id.in_([ids["revoked-used"], ids["revoked-unused"]]))
            .values(revoked_at=func.now())
        )
        await async_session.commit()

        keys = (await _usage(auth_client, headers)).json()["keys"]

        assert {k["name"]: (k["revoked"], k["requests_this_month"]) for k in keys} == {
            "active": (False, 6),
            "revoked-used": (True, 2),
        }

    @pytest.mark.asyncio
    async def test_other_users_usage_is_not_shown(
        self, auth_client, async_session, login, make_user, make_key, clock
    ):
        other = await make_user("free")
        await make_key(other)
        await _flushed(
            async_session,
            (await _key_id(async_session, other), _utc(2026, 10, 9, 12), 8),
        )
        _, headers = await login("free")

        body = (await _usage(auth_client, headers)).json()

        assert body["keys"] == []
        assert sum(day["requests"] for day in body["daily"]) == 0
        assert body["current"]["month"]["used"] == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("days", [0, 91])
    async def test_days_out_of_range(self, auth_client, login, days):
        _, headers = await login()

        assert (await _usage(auth_client, headers, days=days)).status_code == 422

    @pytest.mark.asyncio
    async def test_needs_a_login_token(self, auth_client):
        assert (await auth_client.get("/portal/usage")).status_code == 401
