"""/portal/admin: the Administration page's user management, for admins only."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError

from geonames_server.api import gotrue
from geonames_server.config.config import settings
from geonames_server.database.models.accounts import Profile


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def domain() -> str:
    """An email domain only this test's users have, so searches ignore everyone else."""
    return f"{uuid.uuid4().hex[:12]}.example.com"


@pytest.fixture
def login(make_user, make_jwt):
    async def _login(role: str = "free", email: str | None = None):
        user_id = await make_user(role, email)
        return user_id, _bearer(make_jwt(user_id))

    return _login


@pytest_asyncio.fixture
async def admin(login):
    """(user id, auth headers) of a signed-in admin."""
    return await login("admin")


@pytest.fixture
def as_owner(async_session):
    """Leave geonames_admin: the admin endpoints switch the test's shared session to it."""

    async def _as_owner():
        await async_session.execute(text("RESET ROLE"))

    return _as_owner


async def _profile(async_session, user_id) -> Profile:
    return (
        await async_session.execute(
            select(Profile)
            .where(Profile.id == user_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


class TestAccess:
    @pytest.mark.asyncio
    async def test_needs_a_login_token(self, auth_client):
        resp = await auth_client.get("/portal/admin/users")

        assert resp.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize("role", ["free", "sr_dev"])
    async def test_non_admins_are_refused(self, auth_client, login, role):
        _, headers = await login(role)

        resp = await auth_client.get("/portal/admin/users", headers=headers)

        assert resp.status_code == 403
        assert resp.json()["detail"] == "This page needs an admin account"

    @pytest.mark.asyncio
    async def test_api_keys_are_not_accepted(self, auth_client, admin, make_key):
        key = await make_key(admin[0])

        resp = await auth_client.get("/portal/admin/users", headers={"X-API-Key": key})

        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_me_says_who_is_an_admin(self, auth_client, admin, login):
        _, free_headers = await login("free")

        assert (await auth_client.get("/portal/me", headers=admin[1])).json()[
            "is_admin"
        ]
        assert not (await auth_client.get("/portal/me", headers=free_headers)).json()[
            "is_admin"
        ]


class TestDatabaseRole:
    @pytest.mark.asyncio
    async def test_admin_role_can_change_only_role_and_flags(
        self, async_session, make_user
    ):
        user_id = await make_user()
        await async_session.execute(text("SET LOCAL ROLE geonames_admin"))
        await async_session.execute(
            text(
                "UPDATE app.profiles SET role = 'dev', is_active = false WHERE id = :id"
            ),
            {"id": user_id},
        )

        # The failed statement aborts the transaction; the test rolls it back.
        with pytest.raises(ProgrammingError, match="permission denied"):
            await async_session.execute(
                text("UPDATE app.profiles SET email = 'x@y.z' WHERE id = :id"),
                {"id": user_id},
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "statement",
        [
            "DELETE FROM app.profiles",
            "UPDATE app.api_keys SET revoked_at = now()",
            "UPDATE app.tiers SET per_month = 1",
            "SELECT 1 FROM auth.users",
        ],
    )
    async def test_admin_role_cannot_touch_anything_else(
        self, async_session, statement
    ):
        await async_session.execute(text("SET LOCAL ROLE geonames_admin"))

        with pytest.raises(ProgrammingError, match="permission denied"):
            await async_session.execute(text(statement))


class TestListUsers:
    @pytest.mark.asyncio
    async def test_search_by_email(self, auth_client, admin, make_user, domain):
        await make_user("free", f"alice@{domain}")
        bob = await make_user("dev", f"bob@{domain}")

        resp = await auth_client.get(
            "/portal/admin/users", params={"q": f"BOB@{domain}"}, headers=admin[1]
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        [user] = body["users"]
        assert user == {
            "id": str(bob),
            "email": f"bob@{domain}",
            "role": "dev",
            "tier": "pro",
            "is_admin": False,
            "is_active": True,
            "is_deleted": False,
            "auth_suspended": False,
            "active_keys": 0,
            "created_at": user["created_at"],
        }

    @pytest.mark.asyncio
    async def test_like_wildcards_are_matched_literally(
        self, auth_client, admin, make_user, domain
    ):
        await make_user("free", f"a_b@{domain}")
        await make_user("free", f"axb@{domain}")

        resp = await auth_client.get(
            "/portal/admin/users", params={"q": f"a_b@{domain}"}, headers=admin[1]
        )

        assert [u["email"] for u in resp.json()["users"]] == [f"a_b@{domain}"]

    @pytest.mark.asyncio
    async def test_status_filter(
        self, auth_client, async_session, admin, make_user, domain
    ):
        active = await make_user("free", f"active@{domain}")
        inactive = await make_user("free", f"inactive@{domain}")
        deleted = await make_user("free", f"deleted@{domain}")
        await async_session.execute(
            text("UPDATE app.profiles SET is_active = false WHERE id = :id"),
            {"id": inactive},
        )
        await async_session.execute(
            text(
                "UPDATE app.profiles SET is_active = false, is_deleted = true "
                "WHERE id = :id"
            ),
            {"id": deleted},
        )
        await async_session.commit()

        found = {}
        for status in ("active", "inactive", "deleted"):
            resp = await auth_client.get(
                "/portal/admin/users",
                params={"q": domain, "status": status},
                headers=admin[1],
            )
            found[status] = [u["id"] for u in resp.json()["users"]]

        assert found == {
            "active": [str(active)],
            "inactive": [str(inactive)],
            "deleted": [str(deleted)],
        }

    @pytest.mark.asyncio
    async def test_pages_and_total(self, auth_client, admin, make_user, domain):
        for n in range(3):
            await make_user("free", f"user{n}@{domain}")

        resp = await auth_client.get(
            "/portal/admin/users",
            params={"q": domain, "limit": 2, "offset": 2},
            headers=admin[1],
        )

        body = resp.json()
        assert body["total"] == 3
        assert len(body["users"]) == 1

    @pytest.mark.asyncio
    async def test_counts_active_keys_and_auth_suspension(
        self, auth_client, async_session, admin, make_user, make_key, domain
    ):
        user_id = await make_user("dev", f"keys@{domain}")
        await make_key(user_id, "one")
        await make_key(user_id, "two")
        await async_session.execute(
            text(
                "UPDATE auth.users SET banned_until = now() + interval '1 day' WHERE id = :id"
            ),
            {"id": user_id},
        )
        await async_session.commit()

        resp = await auth_client.get(
            "/portal/admin/users", params={"q": domain}, headers=admin[1]
        )

        [user] = resp.json()["users"]
        assert (user["active_keys"], user["auth_suspended"]) == (2, True)

    @pytest.mark.asyncio
    async def test_get_one_user(self, auth_client, admin, make_user, domain):
        user_id = await make_user("tester", f"one@{domain}")

        resp = await auth_client.get(f"/portal/admin/users/{user_id}", headers=admin[1])
        missing = await auth_client.get(
            f"/portal/admin/users/{uuid.uuid4()}", headers=admin[1]
        )

        assert (resp.status_code, resp.json()["tier"]) == (200, "plus")
        assert missing.status_code == 404


class TestRoles:
    @pytest.mark.asyncio
    async def test_lists_roles_with_their_tier(self, auth_client, admin):
        resp = await auth_client.get("/portal/admin/roles", headers=admin[1])

        assert resp.status_code == 200
        roles = {r["name"]: r for r in resp.json()}
        assert roles["dev"] == {
            "name": "dev",
            "tier": "pro",
            "is_admin": False,
            "is_default": False,
        }
        assert roles["admin"]["is_admin"]


class TestUpdateUser:
    @pytest.mark.asyncio
    async def test_change_plan(self, auth_client, admin, login):
        user_id, headers = await login("free")

        resp = await auth_client.patch(
            f"/portal/admin/users/{user_id}", json={"role": "dev"}, headers=admin[1]
        )

        assert resp.status_code == 200
        assert (resp.json()["role"], resp.json()["tier"]) == ("dev", "pro")
        me = await auth_client.get("/portal/me", headers=headers)
        assert me.json()["tier"] == "pro"

    @pytest.mark.asyncio
    async def test_unknown_role(self, auth_client, admin, make_user):
        user_id = await make_user()

        resp = await auth_client.patch(
            f"/portal/admin/users/{user_id}", json={"role": "nope"}, headers=admin[1]
        )

        assert resp.status_code == 422
        assert resp.json()["detail"] == "Role 'nope' does not exist"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "body",
        [{}, {"role": None}, {"is_active": None}],
        ids=["empty", "null-role", "null-flag"],
    )
    async def test_needs_a_change(self, auth_client, admin, make_user, body):
        user_id = await make_user()

        resp = await auth_client.patch(
            f"/portal/admin/users/{user_id}", json=body, headers=admin[1]
        )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_unknown_user(self, auth_client, admin):
        resp = await auth_client.patch(
            f"/portal/admin/users/{uuid.uuid4()}",
            json={"is_active": False},
            headers=admin[1],
        )

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_deactivate_refuses_login_and_cached_keys_at_once(
        self, auth_client, admin, login, make_key, as_owner
    ):
        user_id, headers = await login("free")
        await as_owner()
        key = await make_key(user_id)
        # The key's lookup is cached now
        assert (
            await auth_client.get("/deletions/", headers={"X-API-Key": key})
        ).status_code != 401

        resp = await auth_client.patch(
            f"/portal/admin/users/{user_id}",
            json={"is_active": False},
            headers=admin[1],
        )

        assert resp.status_code == 200
        assert not resp.json()["is_active"]
        assert (await auth_client.get("/portal/me", headers=headers)).status_code == 403
        assert (
            await auth_client.get("/deletions/", headers={"X-API-Key": key})
        ).status_code == 401

    @pytest.mark.asyncio
    async def test_reactivate(self, auth_client, admin, login):
        user_id, headers = await login("free")
        url = f"/portal/admin/users/{user_id}"
        await auth_client.patch(url, json={"is_active": False}, headers=admin[1])

        resp = await auth_client.patch(url, json={"is_active": True}, headers=admin[1])

        assert resp.json()["is_active"]
        assert (await auth_client.get("/portal/me", headers=headers)).status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("body", "detail"),
        [
            ({"is_active": False}, "You cannot deactivate or delete your own account"),
            ({"is_deleted": True}, "You cannot deactivate or delete your own account"),
            ({"role": "free"}, "You cannot take the admin role away from yourself"),
        ],
    )
    async def test_admin_cannot_lock_themselves_out(
        self, auth_client, admin, body, detail
    ):
        resp = await auth_client.patch(
            f"/portal/admin/users/{admin[0]}", json=body, headers=admin[1]
        )

        assert resp.status_code == 409
        assert resp.json()["detail"] == detail

    @pytest.mark.asyncio
    async def test_admin_may_switch_between_admin_roles(self, auth_client, admin):
        resp = await auth_client.patch(
            f"/portal/admin/users/{admin[0]}",
            json={"role": "admin", "is_active": True},
            headers=admin[1],
        )

        assert resp.status_code == 200


class TestDeleteUser:
    @pytest.mark.asyncio
    async def test_soft_delete_and_restore(
        self, auth_client, async_session, admin, login
    ):
        user_id, headers = await login("free")
        url = f"/portal/admin/users/{user_id}"

        resp = await auth_client.delete(url, headers=admin[1])

        assert resp.status_code == 204
        assert (await _profile(async_session, user_id)).is_deleted
        assert (await auth_client.get("/portal/me", headers=headers)).status_code == 403

        restored = await auth_client.patch(
            url, json={"is_deleted": False}, headers=admin[1]
        )

        assert not restored.json()["is_deleted"]
        assert (await auth_client.get("/portal/me", headers=headers)).status_code == 200

    @pytest.mark.asyncio
    async def test_cannot_delete_yourself(self, auth_client, admin):
        for params in ({}, {"permanent": "true"}):
            resp = await auth_client.delete(
                f"/portal/admin/users/{admin[0]}", params=params, headers=admin[1]
            )
            assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_unknown_user(self, auth_client, admin):
        resp = await auth_client.delete(
            f"/portal/admin/users/{uuid.uuid4()}", headers=admin[1]
        )

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_permanent_delete_only_after_soft_delete(
        self, auth_client, admin, make_user
    ):
        user_id = await make_user()
        url = f"/portal/admin/users/{user_id}"

        with patch.object(gotrue, "delete_user", AsyncMock()) as delete:
            refused = await auth_client.delete(
                url, params={"permanent": "true"}, headers=admin[1]
            )
            await auth_client.delete(url, headers=admin[1])
            removed = await auth_client.delete(
                url, params={"permanent": "true"}, headers=admin[1]
            )

        assert refused.status_code == 409
        assert removed.status_code == 204
        delete.assert_awaited_once_with(user_id)

    @pytest.mark.asyncio
    async def test_permanent_delete_reports_supabase_auth_failure(
        self, auth_client, admin, make_user
    ):
        user_id = await make_user()
        url = f"/portal/admin/users/{user_id}"
        await auth_client.delete(url, headers=admin[1])
        failure = gotrue.GoTrueError("Could not reach Supabase Auth: timed out")

        with patch.object(gotrue, "delete_user", AsyncMock(side_effect=failure)):
            resp = await auth_client.delete(
                url, params={"permanent": "true"}, headers=admin[1]
            )

        assert resp.status_code == 502


class TestCreateUser:
    @pytest.fixture
    def fake_gotrue(self, async_session):
        """GoTrue's create: a row in auth.users, made as the database owner."""
        created = []

        async def _create(email: str, password: str) -> uuid.UUID:
            user_id = uuid.uuid4()
            await async_session.execute(text("SET LOCAL ROLE NONE"))
            await async_session.execute(
                text("INSERT INTO auth.users (id, email) VALUES (:id, :email)"),
                {"id": user_id, "email": email},
            )
            await async_session.execute(text("SET LOCAL ROLE geonames_admin"))
            created.append((email, password))
            return user_id

        with patch.object(gotrue, "create_user", _create):
            yield created

    @pytest.mark.asyncio
    async def test_create_with_role(self, auth_client, admin, fake_gotrue, domain):
        resp = await auth_client.post(
            "/portal/admin/users",
            json={
                "email": f" new@{domain} ",
                "password": "long-enough",
                "role": "tester",
            },
            headers=admin[1],
        )

        assert resp.status_code == 201
        body = resp.json()
        assert (body["email"], body["role"], body["tier"]) == (
            f"new@{domain}",
            "tester",
            "plus",
        )
        assert fake_gotrue == [(f"new@{domain}", "long-enough")]

    @pytest.mark.asyncio
    async def test_create_with_default_role(
        self, auth_client, admin, fake_gotrue, domain
    ):
        resp = await auth_client.post(
            "/portal/admin/users",
            json={"email": f"plain@{domain}", "password": "long-enough"},
            headers=admin[1],
        )

        assert resp.json()["role"] == "free"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "body",
        [
            {"email": "not-an-email", "password": "long-enough"},
            {"email": "a@b.co", "password": "short"},
            {"email": "a@b.co", "password": "long-enough", "role": "nope"},
        ],
        ids=["bad-email", "short-password", "unknown-role"],
    )
    async def test_refused_before_supabase_auth(
        self, auth_client, admin, fake_gotrue, body
    ):
        with patch.object(settings, "password_min_length", 8):
            resp = await auth_client.post(
                "/portal/admin/users", json=body, headers=admin[1]
            )

        assert resp.status_code == 422
        assert fake_gotrue == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error", "status"),
        [
            (
                gotrue.GoTrueError(
                    "A user with this email address has already been registered", 422
                ),
                409,
            ),
            (gotrue.GoTrueError("Password is known to be weak", 422), 422),
            (gotrue.GoTrueError("Could not reach Supabase Auth: refused"), 502),
        ],
        ids=["exists", "rejected", "unreachable"],
    )
    async def test_supabase_auth_errors(self, auth_client, admin, error, status):
        with patch.object(gotrue, "create_user", AsyncMock(side_effect=error)):
            resp = await auth_client.post(
                "/portal/admin/users",
                json={"email": "a@b.co", "password": "long-enough"},
                headers=admin[1],
            )

        assert resp.status_code == status
        assert str(error) in resp.json()["detail"]
