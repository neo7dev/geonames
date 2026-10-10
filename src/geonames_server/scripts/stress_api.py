"""Stress the API and check the rate limits and quotas, as the seeded dev users. Dev only.

Load: concurrent traffic for a while, then latency and statuses per role.

    python -m geonames_server.scripts.stress_api load                      # all roles, 30 s
    python -m geonames_server.scripts.stress_api load --role dev --concurrency 20 --duration 60
    python -m geonames_server.scripts.stress_api load --requests 500 --path /metadata/

Limits: per role, against the live server and Redis.

    python -m geonames_server.scripts.stress_api limits                    # minute limit, admin route
    python -m geonames_server.scripts.stress_api limits --role free --quota hour --quota month

- minute: a concurrent burst of (requests left + extra). Exactly the requests left
  must pass, the rest get 429 with Retry-After, and /portal/usage must agree.
  Tiers without a minute limit must pass a whole burst.
- admin route: /sync/{id} answers 403 to non-admins without counting the call.
- --quota hour/month: reaching those limits for real takes hours, so the user's
  Redis counter is set to a few short of the limit, the same burst is checked,
  and the counter is put back (plus the calls that passed). Needs REDIS_URL and
  DEV_SEED_USERS=true.

The calls are real: they count against the users' limits and are written to
their usage. A check that finds a user already limited says so; wait for the
window to reset (or run the check for another role).
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from redis.asyncio import Redis

from geonames_server.api.rate_limit import WINDOW_NAMES, Windows
from geonames_server.config.config import settings
from geonames_server.scripts.api_client import (
    API_URL,
    ROLES,
    ApiClient,
    DevUser,
    Result,
    dev_users,
    quiet_http_logs,
    random_request,
)

# A cheap route for the limit checks
PATH = "/metadata/"


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


async def run_load(
    client: ApiClient,
    *,
    concurrency: int,
    duration: float | None = None,
    requests: int | None = None,
    path: str | None = None,
    params: dict | None = None,
    seed: int | None = None,
) -> list[Result]:
    """Call with *concurrency* workers until *duration* seconds or *requests* calls."""
    deadline = time.monotonic() + duration if duration else None
    left = requests
    results: list[Result] = []

    async def worker(n: int) -> None:
        nonlocal left
        rng = random.Random(None if seed is None else seed + n)
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                return
            if left is not None:
                if left <= 0:
                    return
                left -= 1
            target, query = (path, params or {}) if path else random_request(rng)
            results.append(await client.call(target, query))

    await asyncio.gather(*(worker(n) for n in range(concurrency)))
    return results


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, round(p / 100 * (len(sorted_values) - 1)))
    return sorted_values[index]


@dataclass(frozen=True)
class Summary:
    role: str
    requests: int
    rps: float
    statuses: Counter
    p50: float
    p95: float
    p99: float
    max: float

    @property
    def failures(self) -> int:
        """Server errors and calls without a response."""
        return sum(
            n for status, n in self.statuses.items() if status == 0 or status >= 500
        )


def summarise(role: str, results: list[Result], seconds: float) -> Summary:
    latencies = sorted(r.seconds * 1000 for r in results)
    return Summary(
        role=role,
        requests=len(results),
        rps=len(results) / seconds if seconds else 0.0,
        statuses=Counter(r.status for r in results),
        p50=_percentile(latencies, 50),
        p95=_percentile(latencies, 95),
        p99=_percentile(latencies, 99),
        max=latencies[-1] if latencies else 0.0,
    )


def print_summaries(summaries: list[Summary]) -> None:
    print(
        f"{'role':<8}{'calls':>7}{'rps':>8}{'2xx':>7}{'429':>6}{'4xx':>6}"
        f"{'5xx':>6}{'none':>6}{'p50 ms':>9}{'p95 ms':>9}{'p99 ms':>9}{'max ms':>9}"
    )
    for s in summaries:
        by_class = Counter()
        for status, n in s.statuses.items():
            if status == 0:
                by_class["none"] += n
            elif status == 429:
                by_class["429"] += n
            else:
                by_class[f"{status // 100}xx"] += n
        print(
            f"{s.role:<8}{s.requests:>7}{s.rps:>8.1f}{by_class['2xx']:>7}"
            f"{by_class['429']:>6}{by_class['4xx']:>6}{by_class['5xx']:>6}"
            f"{by_class['none']:>6}{s.p50:>9.1f}{s.p95:>9.1f}{s.p99:>9.1f}{s.max:>9.1f}"
        )


def print_slowest(results: list[Result], n: int) -> None:
    """The *n* routes with the slowest p95, from the calls that reached them."""
    by_route: dict[str, list[float]] = defaultdict(list)
    for r in results:
        by_route[r.route].append(r.seconds * 1000)
    if not by_route or n <= 0:
        return
    print("\nSlowest routes (p95, without 429s):")
    rows = sorted(
        ((route, sorted(ms)) for route, ms in by_route.items()),
        key=lambda row: _percentile(row[1], 95),
        reverse=True,
    )
    for route, ms in rows[:n]:
        print(
            f"  {_percentile(ms, 95):8.1f} ms p95  {ms[-1]:8.1f} ms max  "
            f"{len(ms):>6} calls  {route}"
        )


async def _load(args: argparse.Namespace) -> int:
    users = dev_users(args.role)
    clients = [ApiClient(u.api_key, base_url=args.base_url) for u in users]
    duration = None if args.requests else args.duration
    print(
        f"Load: {len(users)} user(s) x {args.concurrency} worker(s), "
        + (f"{args.requests} calls each" if args.requests else f"{duration:g} s")
        + f", {args.path or 'random data requests'} on {args.base_url}"
    )
    start = time.monotonic()
    try:
        per_user = await asyncio.gather(
            *(
                run_load(
                    client,
                    concurrency=args.concurrency,
                    duration=duration,
                    requests=args.requests,
                    path=args.path,
                    seed=args.seed,
                )
                for client in clients
            )
        )
    finally:
        await asyncio.gather(*(c.aclose() for c in clients))
    seconds = time.monotonic() - start
    summaries = [
        summarise(u.role, results, seconds) for u, results in zip(users, per_user)
    ]
    summaries.append(
        summarise("total", [r for results in per_user for r in results], seconds)
    )
    print_summaries(summaries)
    print_slowest(
        [r for results in per_user for r in results if r.status != 429], args.slowest
    )
    errors = defaultdict(Counter)
    for u, results in zip(users, per_user):
        for r in results:
            if r.status == 0 or r.status >= 500:
                errors[u.role][f"{r.status} {r.detail or ''}".strip()] += 1
    for role, counts in errors.items():
        for error, n in counts.most_common(3):
            print(f"  {role}: {n} x {error}")
    return 1 if summaries[-1].failures else 0


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    role: str
    name: str
    passed: bool
    message: str

    def __str__(self) -> str:
        return f"{'PASS' if self.passed else 'FAIL'}  {self.role:<7} {self.name:<12} {self.message}"


async def burst(client: ApiClient, n: int, path: str = PATH) -> list[Result]:
    """*n* calls at once."""
    return list(await asyncio.gather(*(client.call(path) for _ in range(n))))


async def check_burst(
    client: ApiClient,
    role: str,
    name: str,
    *,
    allowed: int | None,
    window: str | None,
    extra: int,
    path: str = PATH,
) -> Check:
    """Burst allowed + extra calls: exactly *allowed* pass, the rest get 429 for *window*.

    With *allowed* None (no limit), the *extra* calls must all pass.
    """
    results = await burst(client, (allowed or 0) + extra, path)
    statuses = Counter(r.status for r in results)
    limited = [r for r in results if r.status == 429]
    if allowed is None:
        passed = statuses == Counter({200: extra})
        return Check(role, name, passed, f"no limit: {_statuses(statuses)}")
    problems = []
    if statuses != Counter({200: allowed, 429: extra}):
        problems.append(f"expected {allowed}x200 + {extra}x429")
    if any(not r.retry_after or r.retry_after < 1 for r in limited):
        problems.append("429 without Retry-After")
    wrong = {r.detail for r in limited if f"per {window}" not in (r.detail or "")}
    if wrong:
        problems.append(f"429 not for the {window}: {sorted(wrong)[0]!r}")
    retry = max((r.retry_after or 0 for r in limited), default=0)
    message = f"{_statuses(statuses)}, Retry-After up to {retry}s"
    if problems:
        message += " — " + "; ".join(problems)
    return Check(role, name, not problems, message)


def _statuses(statuses: Counter) -> str:
    return ", ".join(f"{n}x{status}" for status, n in sorted(statuses.items()))


def _current(usage: dict) -> dict[str, dict]:
    return {w: usage["current"][w] for w in WINDOW_NAMES}


def _left(usage: dict) -> dict[str, int | None]:
    """Requests left per window; None without a limit."""
    return {
        w: None if c["limit"] is None else max(c["limit"] - (c["used"] or 0), 0)
        for w, c in _current(usage).items()
    }


async def _fresh_minute(client: ApiClient, margin: float = 10) -> dict:
    """Usage, read early enough in a minute that a burst does not straddle two.

    Also waits for the next minute when this one's limit is used up, e.g. by the
    quota checks on a tier with a low minute limit.
    """
    usage = await client.usage()
    resets = datetime.fromisoformat(usage["current"]["minute"]["resets_at"])
    wait = (resets - datetime.now(timezone.utc)).total_seconds()
    if wait < margin or _left(usage)["minute"] == 0:
        await asyncio.sleep(max(wait, 0) + 1)
        usage = await client.usage()
    return usage


async def check_minute(
    client: ApiClient, role: str, extra: int, unlimited: int
) -> Check:
    usage = await _fresh_minute(client)
    left = _left(usage)
    if all(v is None for v in left.values()):
        return await check_burst(
            client, role, "minute", allowed=None, window=None, extra=unlimited
        )
    window = min(
        (w for w in WINDOW_NAMES if left[w] is not None), key=lambda w: left[w]
    )
    if left[window] == 0:
        return Check(
            role,
            "minute",
            False,
            f"already at the {window} limit; wait for it to reset",
        )
    check = await check_burst(
        client, role, "minute", allowed=left[window], window=window, extra=extra
    )
    if not check.passed or window != "minute":
        return check
    after = _current(await client.usage())["minute"]
    if after["used"] != after["limit"]:
        return Check(
            role,
            "minute",
            False,
            f"{check.message} — /portal/usage shows {after['used']}/{after['limit']}",
        )
    return check


async def check_admin_route(client: ApiClient, role: str, is_admin: bool) -> Check:
    """Non-admins get 403 on /sync and the call is not counted; admins get through."""
    before = _current(await client.usage())["minute"]["used"]
    result = await client.call(f"/sync/{uuid.uuid4().hex}")
    after = _current(await client.usage())["minute"]["used"]
    expected, counted = (404, 1) if is_admin else (403, 0)
    passed = result.status == expected and after - before == counted
    return Check(
        role,
        "admin route",
        passed,
        f"/sync/{{id}} -> {result.status} (want {expected}), counted {after - before} (want {counted})",
    )


async def check_quota(
    client: ApiClient,
    redis: Redis,
    role: str,
    user_id: str,
    window: str,
    room: int,
    extra: int,
) -> Check:
    """Set the *window* counter *room* short of the limit, burst, then put it back."""
    usage = await _fresh_minute(client)
    limit = _current(usage)[window]["limit"]
    if limit is None:
        return Check(role, window, True, "no limit, nothing to check")
    if (_left(usage)["minute"] or room) < room:
        return Check(role, window, False, "not enough of the minute left; rerun later")
    key = Windows.at(time.time()).counter_keys(user_id)[WINDOW_NAMES.index(window)]
    original = await redis.get(key)
    if original is None:
        return Check(role, window, False, f"no Redis counter {key}; is the limiter on?")
    await redis.set(key, limit - room, keepttl=True)
    try:
        check = await check_burst(
            client, role, window, allowed=room, window=window, extra=extra
        )
    finally:
        # Back to the real count, plus the calls that passed
        now = int(await redis.get(key) or limit - room)
        await redis.set(key, int(original) + now - (limit - room), keepttl=True)
    return Check(
        check.role,
        check.name,
        check.passed,
        f"counter at {limit - room}/{limit}: {check.message}",
    )


async def check_user(
    user: DevUser, args: argparse.Namespace, redis: Redis | None
) -> list[Check]:
    async with ApiClient(user.api_key, base_url=args.base_url) as client:
        await client.login(user.email, user.password)
        me = await client.me()
        # Also makes sure the user's Redis counters exist
        warm = await client.call(PATH)
        if warm.status == 429 and "per minute" in (warm.detail or ""):
            print(f"  (minute limit used up; waiting {warm.retry_after}s)")
            await asyncio.sleep(warm.retry_after or 60)
            warm = await client.call(PATH)
        if warm.status == 429:
            return [Check(user.role, "setup", False, f"already limited: {warm.detail}")]
        if warm.status != 200:
            return [
                Check(
                    user.role, "setup", False, f"{PATH} -> {warm.status} {warm.detail}"
                )
            ]
        if not (await client.usage())["live"]:
            return [Check(user.role, "setup", False, "usage is not live: is Redis up?")]

        checks = []
        for window in args.quota:
            checks.append(
                await check_quota(
                    client, redis, user.role, me["id"], window, args.room, args.extra
                )
            )
        checks.append(await check_admin_route(client, user.role, me["role"] == "admin"))
        checks.append(
            await check_minute(client, user.role, args.extra, args.unlimited_burst)
        )
        return checks


async def _limits(args: argparse.Namespace) -> int:
    redis = None
    if args.quota:
        if not settings.dev_seed_users:
            raise SystemExit("--quota writes to Redis: only with DEV_SEED_USERS=true")
        if not settings.redis_url:
            raise SystemExit("--quota needs REDIS_URL")
        redis = Redis.from_url(settings.redis_url)
    failed = 0
    try:
        for user in dev_users(args.role):
            print(f"{user.role} ({user.email})")
            for check in await check_user(user, args, redis):
                print(f"  {check}")
                failed += not check.passed
    finally:
        if redis is not None:
            await redis.aclose()
    print("All checks passed" if not failed else f"{failed} check(s) failed")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--role",
            action="append",
            choices=ROLES,
            help="seeded user to run as; repeat for several (default: all)",
        )
        p.add_argument("--base-url", default=API_URL, help=f"default {API_URL}")

    load = sub.add_parser("load", help="concurrent traffic, then latency and statuses")
    common(load)
    load.add_argument("--concurrency", type=int, default=5, help="workers per user")
    load.add_argument("--duration", type=float, default=30, help="seconds (default 30)")
    load.add_argument(
        "--requests", type=int, help="calls per user instead of a duration"
    )
    load.add_argument(
        "--path", help="call only this route (default: random data requests)"
    )
    load.add_argument("--seed", type=int, help="seed for the random requests")
    load.add_argument(
        "--slowest", type=int, default=5, help="routes to list by p95 (default 5)"
    )
    load.set_defaults(run=_load)

    limits = sub.add_parser("limits", help="check each tier's limits for real")
    common(limits)
    limits.add_argument(
        "--extra", type=int, default=5, help="calls past the limit (default 5)"
    )
    limits.add_argument(
        "--unlimited-burst",
        type=int,
        default=50,
        help="burst size for a tier without a minute limit (default 50)",
    )
    limits.add_argument(
        "--quota",
        action="append",
        choices=["hour", "month"],
        default=[],
        help="also check this quota by setting the Redis counter near the limit",
    )
    limits.add_argument(
        "--room",
        type=int,
        default=3,
        help="calls left when a quota is pre-set (default 3)",
    )
    limits.set_defaults(run=_limits)

    args = parser.parse_args(argv)
    quiet_http_logs()
    sys.exit(asyncio.run(args.run(args)))


if __name__ == "__main__":
    main()
