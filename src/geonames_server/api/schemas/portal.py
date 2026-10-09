import uuid
from datetime import datetime
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
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
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
