"""User accounts: tiers, roles, profiles and API keys, in the ``app`` schema.

Supabase Auth owns users (``auth.users``). A trigger there creates a profile
for every signup with the default role (``roles.is_default``, ``free`` at
first). Tiers and the role -> tier mapping are rows, so they can be edited in
Studio.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    Uuid,
    func,
    text,
)
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

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    # Copy of auth.users.email kept in sync by the trigger, so Studio shows who is who
    email: Mapped[str | None] = mapped_column(Text)
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
    # Filled from the usage counters in phase 5
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Revoked keys are kept so usage history still points at them
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
