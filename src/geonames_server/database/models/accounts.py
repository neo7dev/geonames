"""User accounts: tiers, roles, profiles and API keys, in the ``app`` schema.

Supabase Auth owns users (``auth.users``). A trigger there creates a profile
for every signup with the default role (``roles.is_default``, ``free`` at
first). Tiers and the role -> tier mapping are rows, so they can be edited in
Studio.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from geonames_server.database.models.app_base import APP_SCHEMA, AppBase


class Tier(AppBase):
    """Request limits for a group of roles. NULL means unlimited."""

    __tablename__ = "tiers"
    __table_args__ = (
        CheckConstraint("per_minute > 0", name="tiers_per_minute_positive"),
        CheckConstraint("per_hour > 0", name="tiers_per_hour_positive"),
        CheckConstraint("per_month > 0", name="tiers_per_month_positive"),
        CheckConstraint("max_keys > 0", name="tiers_max_keys_positive"),
    )

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    per_minute: Mapped[int | None] = mapped_column(Integer)
    per_hour: Mapped[int | None] = mapped_column(Integer)
    per_month: Mapped[int | None] = mapped_column(Integer)
    # Active (not revoked) API keys a user may hold
    max_keys: Mapped[int | None] = mapped_column(Integer)


class Role(AppBase):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    tier: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{APP_SCHEMA}.tiers.name", onupdate="CASCADE"), nullable=False
    )
    # Admins can run and inspect syncs
    is_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # The role new profiles get; a deferred trigger keeps exactly one marked
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )


class Profile(AppBase):
    """One row per Supabase Auth user, same id.

    On Supabase the migration adds a FK to auth.users(id) ON DELETE CASCADE; it
    is not declared here because auth.users is not part of this metadata.
    """

    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint(
            "char_length(display_name) BETWEEN 1 AND 100",
            name="profiles_display_name_length",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    # Copy of auth.users.email kept in sync by the trigger, so Studio shows who is who
    email: Mapped[str | None] = mapped_column(Text)
    # The name they go by. Filled from the signup's full_name metadata, then
    # edited on the profile page (or by an admin); NULL when none was given.
    display_name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(
        Text,
        ForeignKey(f"{APP_SCHEMA}.roles.name", onupdate="CASCADE"),
        nullable=False,
        server_default=text("app.default_role()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Copies of auth.users' columns, kept in sync by the trigger. A banned or
    # soft-deleted user's keys and login tokens are refused.
    banned_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set by admins on the Administration page, not copied from auth.users.
    # A deactivated or deleted user's keys and login tokens are refused too.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )


class ApiKey(AppBase):
    """An API key. Only its SHA-256 is stored; the key is shown once at creation."""

    __tablename__ = "api_keys"
    __table_args__ = (
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 64", name="api_keys_name_length"
        ),
        Index(
            "api_keys_active_user_id_idx",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        # Usage queries include revoked keys
        Index("api_keys_user_id_idx", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(f"{APP_SCHEMA}.profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # First characters of the key, to tell keys apart in lists
    prefix: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Set from the usage counters when they are flushed (every few minutes)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Revoked keys are kept so usage history still points at them
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKeyUsage(AppBase):
    """Requests made with a key in one UTC hour, flushed from Redis."""

    __tablename__ = "api_key_usage"
    __table_args__ = (
        CheckConstraint("requests > 0", name="api_key_usage_requests_positive"),
    )

    key_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(f"{APP_SCHEMA}.api_keys.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Start of the UTC hour
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    requests: Mapped[int] = mapped_column(BigInteger, nullable=False)


class UsageFlush(AppBase):
    """A usage batch already written, so a retried flush does not count it twice."""

    __tablename__ = "usage_flushes"

    batch: Mapped[str] = mapped_column(Text, primary_key=True)
    flushed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AccountEvent(AppBase):
    """Something that happened to an account, written by a trigger on profiles.

    Written by the trigger (migration c4d8e2f6a9b1); the app only relabels
    the events of accounts it creates or removes (account_events.py). No
    foreign key, so the history outlives a removed account.
    """

    __tablename__ = "account_events"
    __table_args__ = (
        Index("account_events_created_at_idx", "created_at"),
        Index("account_events_user_id_idx", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # As it was when the event happened
    user_email: Mapped[str | None] = mapped_column(Text)
    # signed_up, created, upgraded, downgraded, role_changed, name_changed,
    # email_changed, deactivated, reactivated, deleted, restored, suspended,
    # unsuspended, removed
    event: Mapped[str] = mapped_column(Text, nullable=False)
    # Before and after values, e.g. from_role / to_role / from_tier / to_tier
    details: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Who did it: NULL for the system or an untagged change (Studio, Supabase Auth)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_email: Mapped[str | None] = mapped_column(Text)
    # user (their own doing), admin, system, external (Supabase Auth or Studio)
    source: Mapped[str] = mapped_column(Text, nullable=False)
