from datetime import date, datetime, time, timezone
from email.utils import format_datetime, parsedate_to_datetime
from zoneinfo import ZoneInfo

# ! Basically not required because parsedate_to_datetime only works with timezone.utc and not with ZoneInfo("UTC")
# ! because it expects an tzinfo object not a ZoneInfo object
UTC = ZoneInfo("UTC")


# ! Either use usegmt=False when using ZoneInfo or use astimezone(timezone.utc).
def rfc2822_to_datetime(rfc2822_str: str) -> datetime:
    dt = parsedate_to_datetime(rfc2822_str).astimezone(timezone.utc)
    return dt


# Purely for knowledge purposes, convert the headers into datetime and then compare
def datetime_to_rfc2822(dt: datetime) -> str:
    return format_datetime(dt, usegmt=True)


# ! Either use usegmt=False when using ZoneInfo or use astimezone(timezone.utc).
# def datetime_to_rfc2822(dt: datetime) -> str:
#     return format_datetime(dt, usegmt=False)


def current_utc_datetime() -> datetime:
    return datetime.now(UTC)


def current_utc_date() -> date:
    return current_utc_datetime().date()


def current_utc_time() -> time:
    return current_utc_datetime().time()
