"""The first admin account, from ADMIN_EMAIL and ADMIN_PASSWORD.

A fresh deployment has no users, and only an admin can create users with
roles on the Administration page. So on startup, while no profile has an admin
role, the server creates a confirmed user with these credentials through
Supabase Auth's admin API and gives it the admin role. Once any admin exists
the settings are ignored, and can be removed from .env.

If the email is already registered, that account is made admin only when its
email is confirmed and its password is ADMIN_PASSWORD: anyone may have signed
up with the address before the operator set it.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.api import gotrue
from geonames_server.api.account_events import claim_creation, tag_actor
from geonames_server.config.config import settings
from geonames_server.database.models.accounts import Profile, Role
from geonames_server.log.mylogger import get_logger

log = get_logger()


class FirstAdmin(StrEnum):
    # An admin already exists; nothing to do
    exists = "exists"
    # No admin, and ADMIN_EMAIL / ADMIN_PASSWORD are not both set
    not_configured = "not_configured"
    # A new user was created and made admin
    created = "created"
    # The email was already registered, with ADMIN_PASSWORD: made admin
    promoted = "promoted"
    # Supabase Auth refused or was unreachable, or another error; tried again
    # on the next start
    failed = "failed"


async def ensure_first_admin(session: AsyncSession) -> FirstAdmin:
    """Create or promote the first admin; never stops startup."""
    try:
        return await _ensure_first_admin(session)
    except Exception:
        await session.rollback()
        log.exception("Could not set up the admin account; trying again on next start")
        return FirstAdmin.failed


async def _ensure_first_admin(session: AsyncSession) -> FirstAdmin:
    has_admin = await session.scalar(
        select(Profile.id)
        .join(Role, Role.name == Profile.role)
        .where(Role.is_admin)
        .limit(1)
    )
    if has_admin is not None:
        return FirstAdmin.exists

    email, password = settings.admin_email, settings.admin_password
    if email is None or password is None:
        log.warning(
            "No admin account yet: set ADMIN_EMAIL and ADMIN_PASSWORD in .env and "
            "restart to create one"
        )
        return FirstAdmin.not_configured

    # The first admin role by name; the accounts migration seeds `admin`.
    role = await session.scalar(
        select(Role.name).where(Role.is_admin).order_by(Role.name).limit(1)
    )
    if role is None:
        log.error("No role has is_admin set, so no admin account can be created")
        return FirstAdmin.failed

    existing = (
        await session.execute(
            text(
                "SELECT id, email_confirmed_at IS NOT NULL AS confirmed, "
                "encrypted_password IS NOT NULL AND encrypted_password <> '' "
                "AND extensions.crypt(:password, encrypted_password) "
                "= encrypted_password AS password_matches "
                "FROM auth.users WHERE lower(email) = lower(:email)"
            ),
            {"email": email, "password": password.get_secret_value()},
        )
    ).one_or_none()
    if existing is not None and not (existing.confirmed and existing.password_matches):
        log.error(
            f"{email} is already registered, but its email is not confirmed or its "
            "password is not ADMIN_PASSWORD, so it was not made admin. Set "
            "ADMIN_PASSWORD to that account's password, or ADMIN_EMAIL to another "
            "address, and restart"
        )
        return FirstAdmin.failed
    user_id = existing.id if existing is not None else None
    outcome = FirstAdmin.promoted
    if user_id is None:
        # Do not keep a transaction open across the call to Supabase Auth.
        await session.commit()
        try:
            user_id = await gotrue.create_user(email, password.get_secret_value())
        except gotrue.GoTrueError as e:
            log.error(f"Could not create the admin account {email}: {e}")
            return FirstAdmin.failed
        outcome = FirstAdmin.created

    # The signup trigger created the profile with the default role.
    await tag_actor(session, None, "system")
    result = await session.execute(
        update(Profile)
        .where(Profile.id == user_id)
        .values(role=role, is_active=True, is_deleted=False)
    )
    if result.rowcount == 0:
        await session.rollback()
        log.error(
            f"The admin account {email} has no profile; is the signup trigger installed?"
        )
        return FirstAdmin.failed
    if outcome is FirstAdmin.created:
        await claim_creation(session, user_id, None, None, "system", role)
    await session.commit()

    if outcome is FirstAdmin.created:
        log.warning(f"Created the admin account {email}; sign in at /ui with it")
    else:
        log.warning(f"{email} was already registered and is now an admin")
    return outcome
