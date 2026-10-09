"""/portal: login token checks, profile and limits, API key management."""

import hashlib
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, text, update

from geonames_server.database.models.accounts import ApiKey, Profile


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
        resp = await auth_client.get("/portal/me", headers=_bearer(make_jwt(uuid.uuid4())))

        assert resp.status_code == 403


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
    async def test_max_tier_is_unlimited(self, auth_client, login):
        _, headers = await login("sr_dev")

        body = (await auth_client.get("/portal/me", headers=headers)).json()

        assert body["tier"] == "max"
        assert body["limits"] == {
            "per_minute": 600,
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

        stored = await async_session.scalar(select(ApiKey).where(ApiKey.user_id == user_id))
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

        names = [k["name"] for k in (await auth_client.get("/portal/keys", headers=headers)).json()]

        assert names == ["second", "first"]

    @pytest.mark.asyncio
    async def test_revoke(self, auth_client, login):
        _, headers = await login()
        created = (await _create_key(auth_client, headers)).json()

        revoked = await auth_client.delete(f"/portal/keys/{created['id']}", headers=headers)
        listed = await auth_client.get("/portal/keys", headers=headers)
        data = await auth_client.get("/deletions/", headers={"X-API-Key": created["key"]})
        again = await auth_client.delete(f"/portal/keys/{created['id']}", headers=headers)

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
        data = await auth_client.get("/deletions/", headers={"X-API-Key": created["key"]})
        assert data.status_code == 200

    @pytest.mark.asyncio
    async def test_keys_of_other_users_are_not_listed(self, auth_client, login):
        _, owner = await login()
        _, other = await login()
        await _create_key(auth_client, owner)

        assert (await auth_client.get("/portal/keys", headers=other)).json() == []


class TestKeyLimits:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("role", "tier", "limit"), [("free", "basic", 1), ("tester", "plus", 3), ("dev", "pro", 5)]
    )
    async def test_tier_caps_active_keys(self, auth_client, login, role, tier, limit):
        _, headers = await login(role)
        for i in range(limit):
            assert (await _create_key(auth_client, headers, f"key {i}")).status_code == 201

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
            assert (await _create_key(auth_client, headers, f"key {i}")).status_code == 201

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
        keys = [(await _create_key(auth_client, headers, f"key {i}")).json()["key"] for i in range(5)]
        await async_session.execute(
            update(Profile).where(Profile.id == user_id).values(role="free")
        )
        await async_session.commit()

        for key in keys:
            data = await auth_client.get("/deletions/", headers={"X-API-Key": key})
            assert data.status_code == 200
        assert (await _create_key(auth_client, headers)).status_code == 409
