import uuid
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints


class Limits(BaseModel):
    # None means unlimited
    per_minute: int | None
    per_hour: int | None
    per_month: int | None
    max_keys: int | None


class MeResponse(BaseModel):
    id: uuid.UUID
    email: str | None
    role: str
    tier: str
    limits: Limits
    active_keys: int


class CreateApiKeyRequest(BaseModel):
    # No control characters: Postgres text cannot store NUL, and the rest only
    # garble key lists.
    name: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=64,
            pattern=r"^[^\x00-\x1f\x7f]*$",
        ),
    ]


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    # Start of the key, to tell keys apart; the key itself is shown only once
    prefix: str
    created_at: datetime
    last_used_at: datetime | None


class CreatedApiKeyResponse(ApiKeyResponse):
    # The full key. It is not stored and cannot be shown again.
    key: str


class UsageWindow(BaseModel):
    # None when unknown: the minute while Redis is down
    used: int | None
    # None means unlimited
    limit: int | None
    resets_at: datetime


class CurrentUsage(BaseModel):
    minute: UsageWindow
    hour: UsageWindow
    month: UsageWindow


class DailyUsage(BaseModel):
    date: date
    requests: int


class KeyUsage(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    revoked: bool
    requests_this_month: int


class UsageResponse(BaseModel):
    tier: str
    # False when the counts could not be read from Redis and come from memory
    # or Postgres instead
    live: bool
    current: CurrentUsage
    # Oldest first, UTC days; from Postgres, so up to USAGE_FLUSH_SECONDS behind
    daily: list[DailyUsage]
    # Active keys, and revoked keys used this month; newest first
    keys: list[KeyUsage]
