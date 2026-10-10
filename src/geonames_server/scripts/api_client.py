"""Call the API as the seeded dev users, or with any API key. Dev only.

As a library (see stress_api.py): dev_users() gives each seeded user's role,
email, password and API key; ApiClient calls the data routes with the key and,
after login(), the portal routes with a login token; random_request() picks a
realistic data request.

As a command:

    python -m geonames_server.scripts.api_client                  # one random call per seeded user
    python -m geonames_server.scripts.api_client --role free --count 5 --path /metadata/
    python -m geonames_server.scripts.api_client --role dev --path /cities15000/filter \\
        --param country_code=DE --param limit=3 --show-body
    python -m geonames_server.scripts.api_client --api-key gn_... --count 3
    python -m geonames_server.scripts.api_client --role tester --portal   # limits and usage

The seeded users and keys come from `make seed-users` (DEV_API_KEY_* in .env).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from geonames_server.config.config import settings
from geonames_server.ingestion.registry import get_enabled_datasets
from geonames_server.scripts.seed_dev_users import (
    DEV_USERS,
    GOTRUE_URL,
    PASSWORD,
    load_keys,
)

API_URL = os.environ.get("API_URL", "http://localhost:9500")
ROLES = [role for role, _, _ in DEV_USERS]


def quiet_http_logs() -> None:
    """The app's logging setup shows httpx's debug lines; the commands do not need them."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


@dataclass(frozen=True)
class DevUser:
    role: str
    api_key: str
    email: str | None = None
    password: str | None = None


def dev_users(roles: list[str] | None = None) -> list[DevUser]:
    """The seeded users, one per role, in DEV_USERS order; only *roles* if given."""
    unknown = set(roles or []) - set(ROLES)
    if unknown:
        raise ValueError(
            f"No seeded user for role(s) {', '.join(sorted(unknown))}; "
            f"seeded: {', '.join(ROLES)}"
        )
    keys = load_keys()
    return [
        DevUser(role=role, api_key=keys[var], email=email, password=PASSWORD)
        for role, email, var in DEV_USERS
        if not roles or role in roles
    ]


@dataclass(frozen=True)
class Result:
    path: str
    status: int  # 0: no response (connection error, timeout)
    seconds: float
    retry_after: int | None = None
    detail: str | None = None
    body: Any = None

    route: str = ""  # path plus the query param names, e.g. /cities500/filter?name

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class ApiClient:
    """One user's view of the API: data routes with the API key, portal with a login.

    Pass *client* to reuse an httpx.AsyncClient (tests pass one bound to the app).
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = API_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10,
    ) -> None:
        self.api_key = api_key
        self.token: str | None = None
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=200),
        )

    async def __aenter__(self) -> ApiClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def call(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        method: str = "GET",
        keep_body: bool = False,
    ) -> Result:
        """Call a data route with the API key. Never raises for HTTP or network errors."""
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        route = path + ("?" + "&".join(sorted(params)) if params else "")
        start = time.perf_counter()
        try:
            resp = await self._client.request(
                method, path, params=params, headers=headers
            )
        except httpx.HTTPError as e:
            return Result(
                path, 0, time.perf_counter() - start, detail=repr(e), route=route
            )
        seconds = time.perf_counter() - start
        body = _json(resp)
        retry_after = resp.headers.get("Retry-After")
        return Result(
            path=path,
            status=resp.status_code,
            seconds=seconds,
            retry_after=int(retry_after) if retry_after else None,
            detail=body.get("detail") if isinstance(body, dict) else None,
            body=body if keep_body else None,
            route=route,
        )

    async def login(
        self, email: str, password: str, *, gotrue_url: str = GOTRUE_URL
    ) -> None:
        """Get a login token from Supabase Auth, for the portal routes."""
        async with httpx.AsyncClient(base_url=gotrue_url, timeout=10) as gotrue:
            resp = await gotrue.post(
                "/token",
                params={"grant_type": "password"},
                json={"email": email, "password": password},
            )
        resp.raise_for_status()
        self.token = resp.json()["access_token"]

    async def portal(self, path: str, **params: Any) -> dict:
        """GET a portal route (/portal/me, /portal/usage) with the login token."""
        if self.token is None:
            raise RuntimeError("Call login() before the portal routes")
        resp = await self._client.get(
            f"/portal{path}",
            params=params,
            headers={"Authorization": f"Bearer {self.token}"},
        )
        resp.raise_for_status()
        return resp.json()

    async def me(self) -> dict:
        return await self.portal("/me")

    async def usage(self) -> dict:
        return await self.portal("/usage", days=1)


def _json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


_COUNTRIES = ["DE", "US", "IN", "FR", "JP", "BR", "GB", "NG", "AU", "MX"]


def random_request(
    rng: random.Random | None = None, datasets: list[str] | None = None
) -> tuple[str, dict[str, Any]]:
    """A realistic data request: (path, query params) over the enabled datasets."""
    rng = rng or random.Random()
    if datasets is None:
        datasets = [ds.name for ds in get_enabled_datasets(settings.enabled_datasets)]
    choices: list[tuple[str, dict[str, Any]]] = [
        ("/metadata/", {}),
        ("/metadata/out-of-sync", {}),
        ("/modifications/", {"limit": 10}),
        ("/deletions/", {"limit": 10}),
    ]
    for name in datasets:
        choices += [
            (f"/{name}/", {"limit": 20, "offset": rng.randrange(0, 1000)}),
            (f"/{name}/filter", {"country_code": rng.choice(_COUNTRIES), "limit": 20}),
            (
                f"/{name}/filter",
                {"min_population": 1_000_000, "order_by": "-population", "limit": 10},
            ),
            (f"/{name}/filter", {"name": rng.choice(["san", "new", "berg", "pur"])}),
        ]
    return rng.choice(choices)


def _params(pairs: list[str]) -> dict[str, str]:
    params = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"--param needs key=value, got {pair!r}")
        params[key] = value
    return params


async def _run(args: argparse.Namespace) -> None:
    if args.api_key:
        users = [DevUser(role="custom", api_key=args.api_key)]
    else:
        users = dev_users(args.role)
    params = _params(args.param)
    for user in users:
        async with ApiClient(user.api_key, base_url=args.base_url) as client:
            if args.portal:
                if user.email is None:
                    raise SystemExit("--portal needs a seeded user, not --api-key")
                await client.login(user.email, user.password)
                me, usage = await client.me(), await client.usage()
                current = {w: usage["current"][w] for w in ("minute", "hour", "month")}
                print(
                    f"{user.role:<7} {me['email']} tier={me['tier']} "
                    f"keys={me['active_keys']}/{me['limits']['max_keys'] or '∞'} "
                    f"live={usage['live']} "
                    + " ".join(
                        f"{w}={c['used']}/{c['limit'] or '∞'}"
                        for w, c in current.items()
                    )
                )
                continue
            for _ in range(args.count):
                path, query = (args.path, params) if args.path else random_request()
                result = await client.call(path, query, keep_body=args.show_body)
                line = (
                    f"{user.role:<7} {result.status:>3} {result.seconds * 1000:7.1f} ms "
                    f"{path} {query or ''}"
                )
                if result.detail:
                    line += f"  ({result.detail})"
                if result.retry_after is not None:
                    line += f"  Retry-After {result.retry_after}s"
                print(line)
                if args.show_body:
                    print(json.dumps(result.body, indent=2, default=str))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Call the API as the seeded dev users (one per role)."
    )
    parser.add_argument(
        "--role",
        action="append",
        choices=ROLES,
        help="seeded user to call as; repeat for several (default: all)",
    )
    parser.add_argument("--api-key", help="call with this key instead of a seeded user")
    parser.add_argument("--path", help="route to call (default: a random data request)")
    parser.add_argument(
        "--param", action="append", default=[], metavar="KEY=VALUE", help="query param"
    )
    parser.add_argument("--count", type=int, default=1, help="calls per user")
    parser.add_argument("--base-url", default=API_URL, help=f"default {API_URL}")
    parser.add_argument("--show-body", action="store_true", help="print the JSON body")
    parser.add_argument(
        "--portal",
        action="store_true",
        help="log in and print each user's tier, limits and current usage",
    )
    args = parser.parse_args(argv)
    quiet_http_logs()
    with unreachable_api_exits(args.base_url):
        asyncio.run(_run(args))


@contextmanager
def unreachable_api_exits(base_url: str) -> Iterator[None]:
    """Turn a refused connection into a one-line hint instead of a traceback."""
    try:
        yield
    except httpx.ConnectError:
        sys.exit(f"API not reachable at {base_url}: start it with make run")


if __name__ == "__main__":
    main()
