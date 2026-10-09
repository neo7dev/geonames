"""make seed-users: one dev user per tier, created through a stand-in for GoTrue."""

import json
import uuid
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select, text, update

from geonames_server.api.auth import hash_api_key
from geonames_server.database.models.accounts import ApiKey, Profile
from geonames_server.scripts import seed_dev_users

MODULE = "geonames_server.scripts.seed_dev_users"

# Not the real dev users, which may already exist in the dev database
TEST_USERS = [
    ("free", "seed-test-free@example.com", "TEST_KEY_FREE"),
    ("admin", "seed-test-admin@example.com", "TEST_KEY_ADMIN"),
]
KEYS = {"TEST_KEY_FREE": "gn_seed_test_free", "TEST_KEY_ADMIN": "gn_seed_test_admin"}


@pytest.fixture(autouse=True)
def _test_users():
    with patch(f"{MODULE}.DEV_USERS", TEST_USERS):
        yield


@pytest.fixture
def gotrue(sync_session):
    """Fake GoTrue admin API: creates the auth.users row like the real one."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        sync_session.execute(
            text("INSERT INTO auth.users (id, email) VALUES (:id, :email)"),
            {"id": uuid.uuid4(), "email": body["email"]},
        )
        return httpx.Response(200, json={})

    client = httpx.Client(base_url="http://auth", transport=httpx.MockTransport(handler))
    client.calls = calls
    yield client
    client.close()


def _seeded(session):
    rows = session.execute(
        select(Profile.email, Profile.role, ApiKey.key_hash, ApiKey.revoked_at)
        .join(ApiKey, ApiKey.user_id == Profile.id)
        .where(Profile.email.in_([email for _, email, _ in TEST_USERS]))
        .order_by(Profile.email)
    )
    return [tuple(row) for row in rows]


def test_creates_confirmed_users_with_roles_and_keys(sync_session, gotrue):
    seed_dev_users.seed(sync_session, gotrue, KEYS)

    assert gotrue.calls == [
        (
            "/admin/users",
            {"email": email, "password": seed_dev_users.PASSWORD, "email_confirm": True},
        )
        for _, email, _ in TEST_USERS
    ]
    assert _seeded(sync_session) == [
        ("seed-test-admin@example.com", "admin", hash_api_key("gn_seed_test_admin"), None),
        ("seed-test-free@example.com", "free", hash_api_key("gn_seed_test_free"), None),
    ]


def test_rerun_restores_roles_and_keys_without_new_users(sync_session, gotrue):
    seed_dev_users.seed(sync_session, gotrue, KEYS)
    sync_session.execute(
        update(Profile)
        .where(Profile.email == "seed-test-admin@example.com")
        .values(role="free")
    )
    sync_session.execute(update(ApiKey).values(revoked_at=text("now()")))
    sync_session.commit()
    gotrue.calls.clear()

    seed_dev_users.seed(sync_session, gotrue, KEYS)

    assert gotrue.calls == []
    assert [(email, role, revoked) for email, role, _, revoked in _seeded(sync_session)] == [
        ("seed-test-admin@example.com", "admin", None),
        ("seed-test-free@example.com", "free", None),
    ]


def test_keys_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("TEST_KEY_FREE", "gn_from_env_free")
    monkeypatch.setenv("TEST_KEY_ADMIN", "gn_from_env_admin")

    assert seed_dev_users.load_keys() == {
        "TEST_KEY_FREE": "gn_from_env_free",
        "TEST_KEY_ADMIN": "gn_from_env_admin",
    }


def test_missing_keys_are_named(monkeypatch):
    monkeypatch.delenv("TEST_KEY_ADMIN", raising=False)
    monkeypatch.setenv("TEST_KEY_FREE", "gn_from_env_free")

    with pytest.raises(SystemExit, match="TEST_KEY_ADMIN"):
        seed_dev_users.load_keys()


def test_refuses_without_dev_seed_users():
    with (
        patch(f"{MODULE}.settings.dev_seed_users", False),
        patch(f"{MODULE}.command") as alembic,
        pytest.raises(SystemExit, match="DEV_SEED_USERS"),
    ):
        seed_dev_users.main()

    alembic.upgrade.assert_not_called()
