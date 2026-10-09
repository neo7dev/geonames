"""UTC windows, counter TTLs and Retry-After for the rate limiter."""

from datetime import datetime, timezone

import pytest

from geonames_server.api.rate_limit import Windows


def _at(*args: int) -> float:
    return datetime(*args, tzinfo=timezone.utc).timestamp()


def test_windows_at_the_start_of_a_minute():
    now = _at(2026, 10, 9, 12, 0, 0)
    windows = Windows.at(now)

    assert windows.minute == int(now) // 60
    assert windows.hour == int(now) // 3600
    assert windows.month == "202610"
    assert [windows.retry_after(w, now) for w in range(3)] == [
        60,
        3600,
        int(_at(2026, 11, 1) - now),
    ]


def test_the_last_second_of_a_minute_waits_one_second():
    now = _at(2026, 10, 9, 12, 0, 59)

    assert Windows.at(now).retry_after(0, now) == 1


def test_retry_after_rounds_up_and_is_never_zero():
    now = _at(2026, 10, 9, 12, 0, 59) + 0.9

    assert Windows.at(now).retry_after(0, now) == 1


def test_the_last_second_of_an_hour():
    now = _at(2026, 10, 9, 12, 59, 59)
    windows = Windows.at(now)

    assert windows.retry_after(0, now) == 1
    assert windows.retry_after(1, now) == 1
    assert Windows.at(now + 1).hour == windows.hour + 1


def test_the_month_window_rolls_over_the_year():
    now = _at(2026, 12, 31, 23, 59, 30)
    windows = Windows.at(now)

    assert windows.month == "202612"
    assert windows.retry_after(2, now) == 30
    assert Windows.at(now + 30).month == "202701"


@pytest.mark.parametrize(
    "year, days_in_february", [pytest.param(2028, 29, id="leap"), (2027, 28)]
)
def test_february(year, days_in_february):
    now = _at(year, 2, 1)

    assert Windows.at(now).retry_after(2, now) == days_in_february * 86400


def test_counters_outlive_their_window_by_a_minute():
    now = _at(2026, 10, 9, 12, 0, 30)

    assert Windows.at(now).ttls(now)[:2] == [90, 3630]


def test_counter_keys_name_the_user_and_window():
    windows = Windows.at(_at(2026, 10, 9, 12, 0, 30))

    assert windows.counter_keys("u1") == [
        f"rl:u1:m:{windows.minute}",
        f"rl:u1:h:{windows.hour}",
        "rl:u1:mo:202610",
    ]
