"""The dev API client and the limit checks of the stress tool, against the app.

The limit checks need TEST_REDIS_URL, like the other rate limit tests.
"""

import random
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from geonames_server.scripts import api_client, stress_api
from geonames_server.scripts.api_client import ApiClient, Result, dev_users
from geonames_server.scripts.stress_api import check_burst, run_load, summarise


@pytest.fixture
def one_call_at_a_time(monkeypatch):
    """Bursts call one after another: the app shares one test DB connection,
    which concurrent calls would wait on, tying it to this test's event loop."""

    async def sequential(client, n, path=stress_api.PATH):
        return [await client.call(path) for _ in range(n)]

    monkeypatch.setattr(stress_api, "burst", sequential)


@pytest.fixture
def seeded_keys(monkeypatch):
    for var in ("FREE", "TESTER", "DEV", "ADMIN"):
        monkeypatch.setenv(f"DEV_API_KEY_{var}", f"gn_{var.lower()}")


def test_dev_users_are_the_seeded_users_by_role(seeded_keys):
    assert [(u.role, u.api_key) for u in dev_users()] == [
        ("free", "gn_free"),
        ("tester", "gn_tester"),
        ("dev", "gn_dev"),
        ("admin", "gn_admin"),
    ]
    assert [u.email for u in dev_users(["admin", "free"])] == [
        "free@geonames.local",
        "admin@geonames.local",
    ]


def test_dev_users_refuses_an_unseeded_role(seeded_keys):
    with pytest.raises(ValueError, match="sr_dev"):
        dev_users(["sr_dev"])


def test_random_requests_cover_the_given_datasets():
    paths = {
        api_client.random_request(random.Random(n), ["cities500"])[0]
        for n in range(200)
    }
    assert {"/cities500/", "/cities500/filter", "/metadata/"} <= paths
    assert not any(p.startswith("/cities1000") for p in paths)


@pytest.mark.asyncio
async def test_run_load_makes_exactly_the_requested_calls():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as http:
        results = await run_load(
            ApiClient("gn_key", client=http),
            concurrency=4,
            requests=10,
            path="/metadata/",
        )

    assert len(results) == len(calls) == 10
    assert all(r.ok and r.route == "/metadata/" for r in results)
    assert all(c.headers["X-API-Key"] == "gn_key" for c in calls)


@pytest.mark.asyncio
async def test_run_load_with_no_time_left_makes_no_calls():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])),
        base_url="http://test",
    ) as http:
        results = await run_load(
            ApiClient("gn_key", client=http), concurrency=2, duration=0
        )

    assert results == []


@pytest.mark.parametrize("option", ["--duration", "--requests", "--concurrency"])
def test_load_refuses_a_count_that_is_not_above_zero(option, capsys):
    with pytest.raises(SystemExit) as exit_:
        stress_api.main(["load", option, "0"])

    assert exit_.value.code == 2
    assert "must be above 0" in capsys.readouterr().err


def test_summarise_counts_statuses_and_failures():
    results = [Result("/", 200, 0.01 * n) for n in range(1, 11)] + [
        Result("/", 429, 0.001),
        Result("/", 503, 0.5),
        Result("/", 0, 1.0),
    ]

    summary = summarise("free", results, seconds=2)

    assert summary.requests == 13
    assert summary.rps == 6.5
    assert summary.statuses == {200: 10, 429: 1, 503: 1, 0: 1}
    assert summary.failures == 2
    assert summary.max == 1000.0


@pytest.mark.asyncio
async def test_the_minute_check_passes_against_the_limiter(
    auth_client, limiter, make_user, make_key, one_call_at_a_time
):
    client = ApiClient(await make_key(await make_user("free")), client=auth_client)

    check = await check_burst(
        client, "free", "minute", allowed=10, window="minute", extra=3
    )

    assert check.passed, check.message
    assert check.message.startswith("10x200, 3x429")


@pytest.mark.parametrize(
    ("left", "window"),
    [
        ({"minute": 3, "hour": 50, "month": 900}, "minute"),
        ({"minute": 10, "hour": 4, "month": 900}, "hour"),
        # A tie goes to the longer window, which the limiter reports
        ({"minute": 10, "hour": 10, "month": 900}, "hour"),
        ({"minute": 5, "hour": 5, "month": 5}, "month"),
        ({"minute": None, "hour": 7, "month": None}, "hour"),
        ({"minute": None, "hour": None, "month": None}, None),
    ],
)
def test_the_burst_targets_the_window_the_limiter_reports(left, window):
    assert stress_api._tightest(left) == window


def _fake_api(*responses: httpx.Response) -> ApiClient:
    """A client whose calls get *responses* in turn."""
    replies = iter(responses)
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: next(replies)),
        base_url="http://test",
    )
    return ApiClient("gn_key", client=http)


def _limited(detail="Rate limit exceeded: 10 requests per minute", retry_after="30"):
    headers = {"Retry-After": retry_after} if retry_after else {}
    return httpx.Response(429, json={"detail": detail}, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("responses", "problem"),
    [
        ([httpx.Response(200, json=[])] * 3, "expected 2x200 + 1x429"),
        (
            [httpx.Response(200, json=[])] * 2 + [_limited(retry_after=None)],
            "Retry-After",
        ),
        (
            [httpx.Response(200, json=[])] * 2
            + [_limited("Rate limit exceeded: 100 requests per hour")],
            "not for the minute",
        ),
    ],
    ids=["not-limited", "no-retry-after", "wrong-window"],
)
async def test_the_burst_check_catches_broken_limits(responses, problem):
    check = await check_burst(
        _fake_api(*responses), "free", "minute", allowed=2, window="minute", extra=1
    )

    assert not check.passed
    assert problem in check.message


@pytest.mark.asyncio
async def test_the_admin_route_check_waits_out_a_minute_about_to_end(monkeypatch):
    """Read late in a minute, the after count would be the next minute's."""
    now = datetime.now(timezone.utc)
    minutes = iter(
        [
            (5, now + timedelta(seconds=2)),  # ends before /sync is answered
            (0, now + timedelta(seconds=60)),  # the next minute
            (0, now + timedelta(seconds=60)),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/portal/usage":
            used, resets = next(minutes)
            window = {"used": used, "limit": 10, "resets_at": resets.isoformat()}
            return httpx.Response(
                200,
                json={"current": {w: window for w in ("minute", "hour", "month")}},
            )
        return httpx.Response(403, json={"detail": "Admins only"})

    async def no_wait(seconds):
        pass

    monkeypatch.setattr(stress_api.asyncio, "sleep", no_wait)
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://t"
    )
    client = ApiClient("gn_key", client=http)
    client.token = "login-token"

    check = await stress_api.check_admin_route(client, "free", is_admin=False)

    assert check.passed, check.message


@pytest.mark.asyncio
async def test_the_admin_route_check_against_the_app(
    auth_client, limiter, make_jwt, make_user, make_key
):
    for role, is_admin in [("free", False), ("admin", True)]:
        user_id = await make_user(role)
        client = ApiClient(await make_key(user_id), client=auth_client)
        client.token = make_jwt(user_id)

        check = await stress_api.check_admin_route(client, role, is_admin)

        assert check.passed, check.message
