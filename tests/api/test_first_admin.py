"""The first admin account comes from ADMIN_EMAIL / ADMIN_PASSWORD while no admin exists."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import select, text

from geonames_server.api import gotrue
from geonames_server.api.first_admin import FirstAdmin, ensure_first_admin
from geonames_server.config.config import Settings, settings
from geonames_server.database.models.accounts import Profile

PASSWORD = "first-admin-password"


@pytest_asyncio.fixture
async def no_admins(async_session):
    """Hide the database's existing admins (rolled back with the test)."""
    await async_session.execute(
        text(
            "UPDATE app.profiles SET role = 'free' WHERE role IN "
            "(SELECT name FROM app.roles WHERE is_admin)"
        )
    )
    await async_session.commit()


@pytest.fixture
def email() -> str:
    return f"admin-{uuid.uuid4().hex[:12]}@example.com"


@pytest.fixture
def configured(email):
    with (
        patch.object(settings, "admin_email", email),
        patch.object(settings, "admin_password", SecretStr(PASSWORD)),
    ):
        yield


@pytest.fixture
def create_user(async_session):
    """Supabase Auth's create_user, adding the auth.users row in the test's transaction."""

    async def _create(email: str, password: str, display_name=None) -> uuid.UUID:
        user_id = uuid.uuid4()
        await async_session.execute(
            text("INSERT INTO auth.users (id, email) VALUES (:id, :email)"),
            {"id": user_id, "email": email},
        )
        return user_id

    with patch.object(gotrue, "create_user", AsyncMock(side_effect=_create)) as mock:
        yield mock


async def _role_of(async_session, email: str) -> str | None:
    return await async_session.scalar(
        select(Profile.role)
        .where(Profile.email == email)
        .execution_options(populate_existing=True)
    )


@pytest.mark.asyncio
async def test_does_nothing_while_an_admin_exists(
    async_session, make_user, configured, email, create_user
):
    await make_user("admin")

    assert await ensure_first_admin(async_session) == FirstAdmin.exists
    create_user.assert_not_called()
    assert await _role_of(async_session, email) is None


@pytest.mark.asyncio
async def test_without_settings_creates_nobody(async_session, no_admins, create_user):
    with (
        patch.object(settings, "admin_email", None),
        patch.object(settings, "admin_password", None),
    ):
        assert await ensure_first_admin(async_session) == FirstAdmin.not_configured
    create_user.assert_not_called()


@pytest.mark.asyncio
async def test_creates_the_admin(
    async_session, no_admins, configured, email, create_user
):
    assert await ensure_first_admin(async_session) == FirstAdmin.created

    create_user.assert_awaited_once_with(email, PASSWORD)
    assert await _role_of(async_session, email) == "admin"
    # The next start finds the admin and leaves it alone.
    assert await ensure_first_admin(async_session) == FirstAdmin.exists
    create_user.assert_awaited_once()


@pytest.mark.asyncio
async def test_promotes_an_existing_user(
    async_session, no_admins, make_user, configured, email, create_user
):
    await make_user("free", email.upper())

    assert await ensure_first_admin(async_session) == FirstAdmin.promoted
    create_user.assert_not_called()
    assert await _role_of(async_session, email.upper()) == "admin"


@pytest.mark.asyncio
async def test_supabase_auth_failure_is_retried_next_start(
    async_session, no_admins, configured, email, create_user
):
    create_user.side_effect = gotrue.GoTrueError("Password should be longer", 422)

    assert await ensure_first_admin(async_session) == FirstAdmin.failed
    assert await _role_of(async_session, email) is None


def test_blank_settings_count_as_unset():
    s = Settings(admin_email=" ", admin_password="")
    assert s.admin_email is None
    assert s.admin_password is None
