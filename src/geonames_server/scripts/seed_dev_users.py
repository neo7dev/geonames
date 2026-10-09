"""Create one dev user per tier, with a known password and API key. Dev only.

Run with `make seed-users`; the devcontainer also runs it on every start. It
refuses unless DEV_SEED_USERS is true, which only .env.dev sets: known
passwords and keys must never reach a deployed server. The keys are real
API keys kept in .env.dev as DEV_API_KEY_FREE, _TESTER, _DEV and _ADMIN.

Users are created through Supabase Auth's admin API, confirmed, so they can
also log in with the password (for /portal). Running it again restores each
user's role and API key (un-revoking it if needed); it never deletes anything.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from alembic import command
from dotenv import dotenv_values, find_dotenv
from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from geonames_server.api.auth import display_prefix, hash_api_key
from geonames_server.config.config import settings
from geonames_server.database.migrations import _alembic_config
from geonames_server.database.models.accounts import ApiKey, Profile
from geonames_server.database.session import sessionLocal

# GoTrue inside the compose network; Kong is not needed for the admin API.
GOTRUE_URL = os.environ.get("GOTRUE_URL", "http://auth:9999")

PASSWORD = "dev-password"

# role, email, variable holding the API key: one user per tier (basic, plus, pro, max)
DEV_USERS = [
    ("free", "free@geonames.local", "DEV_API_KEY_FREE"),
    ("tester", "tester@geonames.local", "DEV_API_KEY_TESTER"),
    ("dev", "dev@geonames.local", "DEV_API_KEY_DEV"),
    ("admin", "admin@geonames.local", "DEV_API_KEY_ADMIN"),
]


def load_keys() -> dict[str, str]:
    """API keys by variable name, from the environment or .env."""
    values = {**dotenv_values(find_dotenv()), **os.environ}
    missing = [var for _, _, var in DEV_USERS if not values.get(var)]
    if missing:
        sys.exit(f"{', '.join(missing)} not set: copy them from .env.dev into .env")
    return {var: values[var] for _, _, var in DEV_USERS}


def _service_token() -> str:
    """A short-lived service_role token, which GoTrue's admin API requires."""
    now = datetime.now(UTC)
    return jwt.encode(
        {"role": "service_role", "iat": now, "exp": now + timedelta(minutes=5)},
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def _user_id(session: Session, email: str):
    return session.scalar(
        text("SELECT id FROM auth.users WHERE email = :email"), {"email": email}
    )


def seed(session: Session, gotrue: httpx.Client, keys: dict[str, str]) -> None:
    for role, email, var in DEV_USERS:
        key = keys[var]
        user_id = _user_id(session, email)
        if user_id is None:
            resp = gotrue.post(
                "/admin/users",
                json={"email": email, "password": PASSWORD, "email_confirm": True},
            )
            resp.raise_for_status()
            user_id = _user_id(session, email)

        # The signup trigger created the profile with the default role.
        updated = session.execute(
            update(Profile).where(Profile.id == user_id).values(role=role)
        )
        if updated.rowcount != 1:
            raise RuntimeError(
                f"{email} has no profile in app.profiles; is the signup trigger installed?"
            )

        session.execute(
            insert(ApiKey)
            .values(
                user_id=user_id,
                name="dev seed",
                prefix=display_prefix(key),
                key_hash=hash_api_key(key),
            )
            .on_conflict_do_update(
                index_elements=[ApiKey.key_hash],
                set_={"user_id": user_id, "revoked_at": None},
            )
        )
    session.commit()


def main() -> None:
    if not settings.dev_seed_users:
        sys.exit(
            "DEV_SEED_USERS is not true: refusing to create users with known "
            "passwords and API keys. It is meant for the devcontainer only."
        )
    if settings.jwt_secret is None:
        sys.exit("JWT_SECRET is not set")
    keys = load_keys()

    # The app tables may not exist yet if the server has never started.
    command.upgrade(_alembic_config("app"), "head")

    with (
        httpx.Client(
            base_url=GOTRUE_URL,
            headers={"Authorization": f"Bearer {_service_token()}"},
            timeout=10,
        ) as gotrue,
        sessionLocal() as session,
    ):
        seed(session, gotrue, keys)

    print(f"Dev users (password for all: {PASSWORD}; API keys in .env):")
    for role, email, var in DEV_USERS:
        print(f"  {role:<7} {email:<24} {var}={keys[var]}")


if __name__ == "__main__":
    main()
