import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from geonames_server.api.schemas.portal import DisplayName


class UserStatus(StrEnum):
    # Neither deactivated nor deleted by an admin
    active = "active"
    # Deactivated by an admin, not deleted
    inactive = "inactive"
    # Soft-deleted by an admin
    deleted = "deleted"


class AdminUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str | None
    display_name: str | None
    role: str
    tier: str
    is_admin: bool
    is_active: bool
    is_deleted: bool
    # Banned or soft-deleted in Supabase Auth (Studio), not on this page
    auth_suspended: bool
    active_keys: int
    created_at: datetime


class AdminUserList(BaseModel):
    # The calling admin first (when they match), then newest first
    users: list[AdminUser]
    # Users matching the search, across all pages
    total: int


class RoleOption(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    tier: str
    is_admin: bool
    # The role new signups get
    is_default: bool


class CreateUserRequest(BaseModel):
    # Supabase Auth checks the address properly; this only catches typos early.
    email: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
        ),
    ]
    # At least PASSWORD_MIN_LENGTH characters, checked by the endpoint
    password: Annotated[str, StringConstraints(max_length=72)]
    display_name: DisplayName | None = None
    # None gives the default role, like a signup
    role: str | None = None


class UpdateUserRequest(BaseModel):
    """Fields left out stay as they are. A null or empty display_name clears it."""

    display_name: DisplayName | None = None
    role: str | None = None
    is_active: bool | None = None
    is_deleted: bool | None = None

    @model_validator(mode="after")
    def _changes_something(self) -> "UpdateUserRequest":
        if not self.model_fields_set:
            raise ValueError(
                "Send at least one of display_name, role, is_active, is_deleted"
            )
        for field in self.model_fields_set - {"display_name"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class AccountEventKind(StrEnum):
    signed_up = "signed_up"
    # By an admin on the Administration page, or the first admin at startup
    created = "created"
    # Role changed to a higher, lower or the same tier
    upgraded = "upgraded"
    downgraded = "downgraded"
    role_changed = "role_changed"
    name_changed = "name_changed"
    email_changed = "email_changed"
    deactivated = "deactivated"
    reactivated = "reactivated"
    deleted = "deleted"
    restored = "restored"
    # Banned or soft-deleted in Supabase Auth, and back
    suspended = "suspended"
    unsuspended = "unsuspended"
    # Removed for good
    removed = "removed"


class EventSource(StrEnum):
    # The account's own user
    user = "user"
    admin = "admin"
    # The server itself (the first admin)
    system = "system"
    # Supabase Auth or Studio
    external = "external"


class AccountEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    user_id: uuid.UUID
    # As it was when it happened; the account may since be removed
    user_email: str | None
    event: AccountEventKind
    # Before and after: from_role / to_role / from_tier / to_tier for plan
    # changes, from / to for name and email changes, role for new accounts
    details: dict[str, Any]
    actor_id: uuid.UUID | None
    actor_email: str | None
    source: EventSource


class AccountEventList(BaseModel):
    # Newest first
    events: list[AccountEvent]
    # Events matching the filters, across all pages
    total: int
