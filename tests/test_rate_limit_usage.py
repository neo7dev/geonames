"""Parsing the USAGE_PENDING hash the rate limit script writes."""

import uuid
from datetime import datetime, timezone

from geonames_server.api.rate_limit import Usage, usage_fields

KEY = uuid.UUID(int=7)
HOUR = int(datetime(2026, 10, 9, 12, tzinfo=timezone.utc).timestamp()) // 3600


def test_from_hash_reads_the_fields_the_script_writes():
    requests_field, last_used_field = usage_fields(KEY, HOUR)
    used_at = int(datetime(2026, 10, 9, 12, 34, 56, tzinfo=timezone.utc).timestamp())

    usage = Usage.from_hash(
        {requests_field.encode(): b"3", last_used_field.encode(): str(used_at).encode()}
    )

    assert usage.requests == {(KEY, datetime(2026, 10, 9, 12, tzinfo=timezone.utc)): 3}
    assert usage.last_used == {
        KEY: datetime(2026, 10, 9, 12, 34, 56, tzinfo=timezone.utc)
    }
    assert usage.total() == 3


def test_from_hash_ignores_unknown_fields():
    usage = Usage.from_hash({f"x:{KEY}:{HOUR}".encode(): b"9"})

    assert not usage
    assert usage.total() == 0
