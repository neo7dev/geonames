"""The first admin account, from ADMIN_EMAIL and ADMIN_PASSWORD.

A fresh deployment has no users, and only an admin can create users with
roles on the Administration page. So on startup, while no profile has an admin
role, the server creates a confirmed user with these credentials through
Supabase Auth's admin API and gives it the admin role. Once any admin exists
the settings are ignored, and can be removed from .env.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.api import gotrue
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
    # The email was already registered: that user was made admin
    promoted = "promoted"
    # Supabase Auth refused or was unreachable; tried again on the next start
    failed = "failed"


async def ensure_first_admin(session: AsyncSession) -> FirstAdmin:
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

    user_id = await session.scalar(
        text("SELECT id FROM auth.users WHERE lower(email) = lower(:email)"),
        {"email": email},
    )
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
    await session.commit()

    if outcome is FirstAdmin.created:
        log.warning(f"Created the admin account {email}; sign in at /ui with it")
    else:
        # The password stays the one the user set.
        log.warning(f"{email} was already registered and is now an admin")
    return outcome
