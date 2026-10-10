"""Reset users' rate limit counters, for testing and stress testing. Dev only.

    python -m geonames_server.scripts.reset_usage                     # hour and month, seeded users
    python -m geonames_server.scripts.reset_usage --role free --window minute --window hour
    python -m geonames_server.scripts.reset_usage --email someone@example.com --all

The limits are enforced from Redis counters, one per window. This sets the
chosen windows' counters to 0 for the current minute, hour and month, so the
next request counts from 1. The counters are set rather than deleted: a
missing month counter would be rebuilt from the usage in Postgres. That usage
history (the portal's daily chart and per-key totals) is left as it is, so
after a month reset the portal's history and current counts disagree.

Refuses unless DEV_SEED_USERS is true, like make seed-users.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid

from redis.asyncio import Redis
from sqlalchemy import select

from geonames_server.api.rate_limit import WINDOW_NAMES, Windows
from geonames_server.config.config import settings
from geonames_server.database.models.accounts import Profile
from geonames_server.database.session import sessionLocal
from geonames_server.scripts.seed_dev_users import DEV_USERS

ROLES = [role for role, _, _ in DEV_USERS]
DEFAULT_WINDOWS = ["hour", "month"]


async def reset_counters(
    redis: Redis, user_id: uuid.UUID | str, windows: list[str], now: float
) -> dict[str, int | None]:
    """Set *user_id*'s counters for *windows* to 0; return their previous values."""
    current = Windows.at(now)
    keys, ttls = current.counter_keys(user_id), current.ttls(now)
    indexes = [WINDOW_NAMES.index(w) for w in windows]
    async with redis.pipeline(transaction=True) as pipe:
        for i in indexes:
            pipe.get(keys[i])
        for i in indexes:
            pipe.set(keys[i], 0, ex=ttls[i])
        replies = await pipe.execute()
    return {
        w: None if value is None else int(value)
        for w, value in zip(windows, replies[: len(indexes)])
    }


def user_ids(emails: list[str]) -> dict[str, uuid.UUID]:
    """Profile ids by email; exits if any email has no profile."""
    with sessionLocal() as session:
        found = dict(
            session.execute(
                select(Profile.email, Profile.id).where(Profile.email.in_(emails))
            ).all()
        )
    missing = [e for e in emails if e not in found]
    if missing:
        sys.exit(f"No profile for {', '.join(missing)}")
    return {e: found[e] for e in emails}


async def _run(args: argparse.Namespace) -> None:
    windows = (
        list(WINDOW_NAMES)
        if args.all
        else [w for w in WINDOW_NAMES if w in (args.window or DEFAULT_WINDOWS)]
    )
    emails = args.email or [
        email for role, email, _ in DEV_USERS if not args.role or role in args.role
    ]
    ids = user_ids(emails)
    redis = Redis.from_url(settings.redis_url)
    try:
        now = time.time()
        for email, user_id in ids.items():
            before = await reset_counters(redis, user_id, windows, now)
            print(
                f"{email:<28} "
                + "  ".join(
                    f"{w} {'-' if n is None else n} -> 0" for w, n in before.items()
                )
            )
    finally:
        await redis.aclose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    who = parser.add_mutually_exclusive_group()
    who.add_argument(
        "--role",
        action="append",
        choices=ROLES,
        help="seeded user to reset; repeat for several (default: all seeded users)",
    )
    who.add_argument(
        "--email", action="append", help="any user, by email; repeat for several"
    )
    parser.add_argument(
        "--window",
        action="append",
        choices=WINDOW_NAMES,
        help="window to reset; repeat for several (default: hour and month)",
    )
    parser.add_argument("--all", action="store_true", help="minute, hour and month")
    args = parser.parse_args(argv)

    if not settings.dev_seed_users:
        sys.exit(
            "DEV_SEED_USERS is not true: refusing to reset rate limits. "
            "This is for the devcontainer only."
        )
    if not settings.redis_url:
        sys.exit("REDIS_URL is not set: there are no counters to reset")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
