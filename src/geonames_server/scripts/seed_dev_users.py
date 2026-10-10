"""Create one dev user per tier, with a known password and API key. Dev only.

Run with `make seed-users`; the devcontainer also runs it on every start, with
--skip-unconfigured so an older .env without the dev settings does not fail the
start. It refuses unless DEV_SEED_USERS is true, which only .env.dev sets: known
passwords and keys must never reach a deployed server. The keys are real
API keys kept in .env.dev as DEV_API_KEY_FREE, _TESTER, _DEV and _ADMIN.

Users are created through Supabase Auth's admin API, confirmed, so they can
also log in with the password (for /portal). Running it again restores each
user's role and API key (un-revoking it if needed), unless that would put the
user over their tier's key limit; it never deletes anything.
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx
from dotenv import dotenv_values, find_dotenv
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from geonames_server.api.auth import display_prefix, hash_api_key
from geonames_server.api.gotrue import service_token
from geonames_server.config.config import settings
from geonames_server.database.migrations import upgrade_app_schema
from geonames_server.database.models.accounts import ApiKey, Profile, Role, Tier
from geonames_server.database.session import sessionLocal

PASSWORD = "dev-password"

# role, email, variable holding the API key: one user per tier (basic, plus, pro, max)
DEV_USERS = [
    ("free", "free@geonames.local", "DEV_API_KEY_FREE"),
    ("tester", "tester@geonames.local", "DEV_API_KEY_TESTER"),
    ("dev", "dev@geonames.local", "DEV_API_KEY_DEV"),
    ("admin", "admin@geonames.local", "DEV_API_KEY_ADMIN"),
]


class NotConfigured(Exception):
    """DEV_SEED_USERS or the dev API keys are not set."""


def load_keys() -> dict[str, str]:
    """API keys by variable name, from the environment or .env."""
    values = {**dotenv_values(find_dotenv()), **os.environ}
    missing = [var for _, _, var in DEV_USERS if not values.get(var)]
    if missing:
        raise NotConfigured(
            f"{', '.join(missing)} not set: copy them from .env.dev into .env"
        )
    return {var: values[var] for _, _, var in DEV_USERS}


def _user_id(session: Session, email: str):
    return session.scalar(
        text("SELECT id FROM auth.users WHERE email = :email"), {"email": email}
    )


def _has_free_key_slot(session: Session, user_id, role: str, key_hash: str) -> bool:
    """Whether the seed key fits in the tier's key limit next to the user's other keys."""
    max_keys = session.scalar(
        select(Tier.max_keys)
        .join(Role, Role.tier == Tier.name)
        .where(Role.name == role)
    )
    if max_keys is None:
        return True
    other_keys = session.scalar(
        select(func.count(ApiKey.id)).where(
            ApiKey.user_id == user_id,
            ApiKey.revoked_at.is_(None),
            ApiKey.key_hash != key_hash,
        )
    )
    return other_keys < max_keys


def seed(session: Session, gotrue: httpx.Client, keys: dict[str, str]) -> list[str]:
    """Create or restore the dev users; return the emails whose key was not restored."""
    skipped = []
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

        if not _has_free_key_slot(session, user_id, role, hash_api_key(key)):
            skipped.append(email)
            continue
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
    return skipped


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--skip-unconfigured",
        action="store_true",
        help="exit successfully, without seeding, if DEV_SEED_USERS or the keys are not set",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    try:
        if not settings.dev_seed_users:
            raise NotConfigured(
                "DEV_SEED_USERS is not true: refusing to create users with known "
                "passwords and API keys. It is meant for the devcontainer only."
            )
        keys = load_keys()
    except NotConfigured as e:
        if args.skip_unconfigured:
            print(f"Skipping dev users: {e} (run `make switch2dev` to set them)")
            return
        sys.exit(str(e))
    if settings.jwt_secret is None:
        sys.exit("JWT_SECRET is not set")

    # The app tables may not exist yet if the server has never started.
    upgrade_app_schema()

    with (
        httpx.Client(
            base_url=settings.gotrue_url,
            headers={"Authorization": f"Bearer {service_token()}"},
            timeout=10,
        ) as gotrue,
        sessionLocal() as session,
    ):
        skipped = seed(session, gotrue, keys)

    print(f"Dev users (password for all: {PASSWORD}; API keys in .env):")
    for role, email, var in DEV_USERS:
        note = "  (not restored: tier key limit reached)" if email in skipped else ""
        print(f"  {role:<7} {email:<24} {var}={keys[var]}{note}")
    if skipped:
        print(
            "Revoke a key for those users on /portal/keys, then rerun to restore the seed key."
        )


if __name__ == "__main__":
    main()
