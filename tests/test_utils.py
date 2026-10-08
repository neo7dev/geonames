from datetime import date, datetime, time, timezone

import pytest

from geonames_server.utils.utils import (
    current_utc_date,
    current_utc_datetime,
    current_utc_time,
    datetime_to_rfc2822,
    rfc2822_to_datetime,
)


class TestRfc2822ToDatetime:
    def test_parses_standard_rfc2822(self):
        result = rfc2822_to_datetime("Tue, 15 Nov 2024 08:12:31 GMT")
        assert result.year == 2024
        assert result.month == 11
        assert result.day == 15
        assert result.hour == 8
        assert result.minute == 12
        assert result.second == 31
        assert result.tzinfo == timezone.utc

    def test_returns_utc_timezone(self):
        result = rfc2822_to_datetime("Wed, 01 Jan 2025 00:00:00 GMT")
        assert result.tzinfo == timezone.utc

    def test_converts_non_utc_to_utc(self):
        # +0500 offset should be converted to UTC
        result = rfc2822_to_datetime("Mon, 10 Feb 2025 15:30:00 +0500")
        assert result.tzinfo == timezone.utc
        assert result.hour == 10
        assert result.minute == 30

    def test_raises_on_invalid_string(self):
        with pytest.raises(Exception):
            rfc2822_to_datetime("not-a-date")


class TestDatetimeToRfc2822:
    def test_formats_utc_datetime(self):
        dt = datetime(2025, 3, 15, 12, 30, 0, tzinfo=timezone.utc)
        result = datetime_to_rfc2822(dt)
        assert "15 Mar 2025" in result
        assert "12:30:00" in result
        assert "GMT" in result

    def test_roundtrip(self):
        dt = datetime(2025, 6, 1, 8, 0, 0, tzinfo=timezone.utc)
        rfc_str = datetime_to_rfc2822(dt)
        parsed_back = rfc2822_to_datetime(rfc_str)
        assert parsed_back == dt


class TestCurrentUtcDatetime:
    def test_returns_datetime(self):
        result = current_utc_datetime()
        assert isinstance(result, datetime)

    def test_has_timezone_info(self):
        result = current_utc_datetime()
        assert result.tzinfo is not None


class TestCurrentUtcDate:
    def test_returns_date(self):
        result = current_utc_date()
        assert isinstance(result, date)

    def test_matches_current_date(self):
        result = current_utc_date()
        assert result == datetime.now(timezone.utc).date()


class TestCurrentUtcTime:
    def test_returns_time(self):
        result = current_utc_time()
        assert isinstance(result, time)
