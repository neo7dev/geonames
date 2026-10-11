"""The account log: who signed up, was created, changed plan or was removed, and by whom."""

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from geonames_server.api import gotrue
from geonames_server.database.models.accounts import AccountEvent


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def domain() -> str:
    """An email domain only this test's users have."""
    return f"{uuid.uuid4().hex[:12]}.example.com"


@pytest.fixture
def login(make_user, make_jwt, domain):
    async def _login(role: str = "free", name: str = "user"):
        user_id = await make_user(role, f"{name}@{domain}")
        return user_id, _bearer(make_jwt(user_id))

    return _login


@pytest_asyncio.fixture
async def admin(login):
    """(user id, auth headers) of a signed-in admin."""
    return await login("admin", "admin")


@pytest.fixture
def events(async_session):
    """The events of one account, oldest first, as the server's own user."""

    async def _events(user_id: uuid.UUID) -> list[AccountEvent]:
        await async_session.execute(text("RESET ROLE"))
        rows = await async_session.scalars(
            select(AccountEvent)
            .where(AccountEvent.user_id == user_id)
            .order_by(AccountEvent.id)
            .execution_options(populate_existing=True)
        )
        return list(rows)

    return _events


def _kinds(rows: list[AccountEvent]) -> list[str]:
    return [row.event for row in rows]


@pytest.mark.asyncio
async def test_a_signup_is_logged_as_the_users_own(login, events, domain):
    user_id, _ = await login()

    [event] = await events(user_id)
    assert (event.event, event.source, event.actor_id) == ("signed_up", "user", user_id)
    assert event.user_email == f"user@{domain}"
    assert event.details == {"role": "free"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start", "to", "kind", "tiers"),
    [
        ("free", "dev", "upgraded", ("basic", "pro")),
        ("dev", "tester", "downgraded", ("pro", "plus")),
        ("admin", "sr_dev", "role_changed", ("max", "max")),
    ],
)
async def test_plan_changes_say_which_way(
    auth_client, admin, login, events, start, to, kind, tiers
):
    user_id, _ = await login(start)

    resp = await auth_client.patch(
        f"/portal/admin/users/{user_id}", json={"role": to}, headers=admin[1]
    )

    assert resp.status_code == 200
    event = (await events(user_id))[-1]
    assert event.event == kind
    assert event.details == {
        "from_role": start,
        "to_role": to,
        "from_tier": tiers[0],
        "to_tier": tiers[1],
    }
    assert (event.source, event.actor_id) == ("admin", admin[0])
    assert event.actor_email.startswith("admin@")


@pytest.mark.asyncio
async def test_admin_flags_are_logged(auth_client, admin, login, events):
    user_id, _ = await login()
    for change in (
        {"is_active": False},
        {"is_active": True},
        {"is_deleted": True},
        {"is_deleted": False},
        {"display_name": "Renamed"},
    ):
        resp = await auth_client.patch(
            f"/portal/admin/users/{user_id}", json=change, headers=admin[1]
        )
        assert resp.status_code == 200

    rows = await events(user_id)
    assert _kinds(rows)[1:] == [
        "deactivated",
        "reactivated",
        "deleted",
        "restored",
        "name_changed",
    ]
    assert {row.source for row in rows[1:]} == {"admin"}
    assert rows[-1].details == {"from": None, "to": "Renamed"}


@pytest.mark.asyncio
async def test_renaming_yourself_is_your_own(auth_client, login, events):
    user_id, headers = await login()

    resp = await auth_client.patch(
        "/portal/me", json={"display_name": "Me"}, headers=headers
    )

    assert resp.status_code == 200
    event = (await events(user_id))[-1]
    assert (event.event, event.source, event.actor_id) == (
        "name_changed",
        "user",
        user_id,
    )


@pytest.mark.asyncio
async def test_changes_from_studio_or_auth_are_external(
    async_session, login, events, domain
):
    user_id, _ = await login()

    # Studio: a plain UPDATE; Supabase Auth: a ban and an email change on auth.users
    await async_session.execute(
        text("UPDATE app.profiles SET role = 'tester' WHERE id = :id"), {"id": user_id}
    )
    await async_session.execute(
        text(
            "UPDATE auth.users SET banned_until = now() + interval '1 day', "
            "email = :email WHERE id = :id"
        ),
        {"id": user_id, "email": f"new@{domain}"},
    )
    await async_session.execute(
        text("UPDATE auth.users SET banned_until = NULL WHERE id = :id"),
        {"id": user_id},
    )

    rows = await events(user_id)
    assert _kinds(rows)[1:] == ["upgraded", "email_changed", "suspended", "unsuspended"]
    assert {(row.source, row.actor_id) for row in rows[1:]} == {("external", None)}
    assert rows[2].details == {"from": f"user@{domain}", "to": f"new@{domain}"}


@pytest.mark.asyncio
async def test_lifting_a_ban_that_ran_out_is_logged(async_session, login, events):
    user_id, _ = await login()
    await async_session.execute(
        text(
            "UPDATE auth.users SET banned_until = now() + interval '1 day' WHERE id = :id"
        ),
        {"id": user_id},
    )
    # The ban runs out: time passes, no row changes.
    await async_session.execute(
        text("ALTER TABLE app.profiles DISABLE TRIGGER app_record_account_event")
    )
    await async_session.execute(
        text(
            "UPDATE app.profiles SET banned_until = now() - interval '1 hour' WHERE id = :id"
        ),
        {"id": user_id},
    )
    await async_session.execute(
        text("ALTER TABLE app.profiles ENABLE TRIGGER app_record_account_event")
    )
    # A rename says nothing about the ban; Supabase Auth clearing it does.
    await async_session.execute(
        text("UPDATE app.profiles SET display_name = 'Bo' WHERE id = :id"),
        {"id": user_id},
    )
    await async_session.execute(
        text("UPDATE auth.users SET banned_until = NULL WHERE id = :id"),
        {"id": user_id},
    )

    rows = await events(user_id)
    assert _kinds(rows)[1:] == ["suspended", "name_changed", "unsuspended"]


@pytest.mark.asyncio
async def test_an_admin_created_account_names_its_creator(
    auth_client, admin, async_session, events, domain
):
    async def _create(email, password, display_name=None):
        # Supabase Auth inserts the user, then writes its metadata separately.
        user_id = uuid.uuid4()
        await async_session.execute(text("SET LOCAL ROLE NONE"))
        await async_session.execute(
            text("INSERT INTO auth.users (id, email) VALUES (:id, :email)"),
            {"id": user_id, "email": email},
        )
        await async_session.execute(text("SET LOCAL ROLE geonames_admin"))
        return user_id

    with patch.object(gotrue, "create_user", _create):
        resp = await auth_client.post(
            "/portal/admin/users",
            json={"email": f"made@{domain}", "password": "long-enough", "role": "dev"},
            headers=admin[1],
        )

    assert resp.status_code == 201
    [row] = await events(uuid.UUID(resp.json()["id"]))
    assert (row.event, row.details) == ("created", {"role": "dev"})
    assert (row.source, row.actor_id) == ("admin", admin[0])


@pytest.mark.asyncio
async def test_removal_is_credited_to_the_admin_and_outlives_the_account(
    auth_client, admin, login, async_session, events
):
    user_id, _ = await login()
    await auth_client.delete(f"/portal/admin/users/{user_id}", headers=admin[1])

    async def _delete(uid):
        # Supabase Auth deletes auth.users; the FK cascade removes the profile.
        await async_session.execute(text("SET LOCAL ROLE NONE"))
        await async_session.execute(
            text("DELETE FROM auth.users WHERE id = :id"), {"id": uid}
        )

    with patch.object(gotrue, "delete_user", _delete):
        resp = await auth_client.delete(
            f"/portal/admin/users/{user_id}?permanent=true", headers=admin[1]
        )

    assert resp.status_code == 204
    rows = await events(user_id)
    assert _kinds(rows) == ["signed_up", "deleted", "removed"]
    assert (rows[-1].source, rows[-1].actor_id) == ("admin", admin[0])


class TestListEvents:
    @pytest.mark.asyncio
    async def test_admins_only(self, auth_client, login):
        _, headers = await login("dev")
        resp = await auth_client.get("/portal/admin/events", headers=headers)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_newest_first_with_filters(self, auth_client, admin, login, domain):
        user_id, _ = await login(name="target")
        for role in ("dev", "free"):
            await auth_client.patch(
                f"/portal/admin/users/{user_id}", json={"role": role}, headers=admin[1]
            )

        resp = await auth_client.get(
            "/portal/admin/events", params={"user_id": str(user_id)}, headers=admin[1]
        )
        body = resp.json()
        assert resp.status_code == 200
        assert body["total"] == 3
        assert [e["event"] for e in body["events"]] == [
            "downgraded",
            "upgraded",
            "signed_up",
        ]
        assert body["events"][0]["actor_email"] == f"admin@{domain}"

        resp = await auth_client.get(
            "/portal/admin/events",
            params=[
                ("q", f"target@{domain}"),
                ("event", "upgraded"),
                ("event", "signed_up"),
            ],
            headers=admin[1],
        )
        assert [e["event"] for e in resp.json()["events"]] == ["upgraded", "signed_up"]

        resp = await auth_client.get(
            "/portal/admin/events",
            params={"user_id": str(user_id), "limit": 1, "offset": 1},
            headers=admin[1],
        )
        assert [e["event"] for e in resp.json()["events"]] == ["upgraded"]
        assert resp.json()["total"] == 3

    @pytest.mark.asyncio
    async def test_unknown_event_kind_is_refused(self, auth_client, admin):
        resp = await auth_client.get(
            "/portal/admin/events", params={"event": "anything"}, headers=admin[1]
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admins_cannot_write_the_log(async_session):
    await async_session.execute(text("SET LOCAL ROLE geonames_admin"))
    with pytest.raises(Exception, match="permission denied"):
        async with async_session.begin_nested():
            await async_session.execute(text("DELETE FROM app.account_events"))
    await async_session.execute(text("RESET ROLE"))
