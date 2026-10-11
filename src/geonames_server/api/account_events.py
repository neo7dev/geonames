"""Who changed an account, for the account_events log.

The trigger on app.profiles writes the log and reads the actor from two
settings of the current transaction. Tag a transaction before changing a
profile in it; the tag ends with the transaction, so tag again after a commit.
Changes without a tag are logged as external (Supabase Auth or Studio).
"""

import uuid
from typing import Literal

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.database.models.accounts import AccountEvent

Source = Literal["user", "admin", "system"]


async def tag_actor(
    session: AsyncSession,
    actor_id: uuid.UUID | None,
    source: Source,
    db_role: str | None = None,
) -> None:
    """Tag the transaction; with *db_role*, also SET LOCAL ROLE, in the same query."""
    set_role = ", set_config('role', :db_role, true)" if db_role else ""
    await session.execute(
        text(
            "SELECT set_config('app.actor_id', :actor, true), "
            f"set_config('app.actor_source', :source, true){set_role}"
        ),
        {
            "actor": str(actor_id) if actor_id else "",
            "source": source,
            "db_role": db_role,
        },
    )


async def _relabel(
    session: AsyncSession, user_id: uuid.UUID, current: str, **values
) -> None:
    """Change the user's latest *current* event; the log is otherwise trigger-written."""
    latest = (
        select(AccountEvent.id)
        .where(AccountEvent.user_id == user_id, AccountEvent.event == current)
        .order_by(AccountEvent.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    # The log is the server's own: geonames_admin may only read it.
    await session.execute(text("SET LOCAL ROLE NONE"))
    await session.execute(
        update(AccountEvent).where(AccountEvent.id == latest).values(**values)
    )


async def claim_creation(
    session: AsyncSession,
    user_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    actor_email: str | None,
    source: Source,
    role: str | None = None,
) -> None:
    """Mark an account the API created as created, by an admin or the server.

    Supabase Auth creates the profile in its own transaction, which the
    trigger logs as the user's own signup. Pass the *role* the account was
    given after that, in this transaction: the event then names it, and the
    role change the trigger logged is part of the creation, so it is dropped.
    """
    values = {}
    if role is not None:
        values["details"] = {"role": role}
    await _relabel(
        session,
        user_id,
        "signed_up",
        event="created",
        actor_id=actor_id,
        actor_email=actor_email,
        source=source,
        **values,
    )
    if role is not None:
        await session.execute(
            delete(AccountEvent).where(
                AccountEvent.user_id == user_id,
                AccountEvent.event.in_(["upgraded", "downgraded", "role_changed"]),
                # Logged in this transaction
                AccountEvent.created_at == func.now(),
            )
        )


async def claim_removal(
    session: AsyncSession,
    user_id: uuid.UUID,
    actor_id: uuid.UUID,
    actor_email: str | None,
) -> None:
    """Credit an admin with a removal Supabase Auth carried out.

    Removing a user deletes them in Supabase Auth, whose cascade deletes the
    profile in its own transaction, so the trigger logs it as external.
    """
    await _relabel(
        session,
        user_id,
        "removed",
        actor_id=actor_id,
        actor_email=actor_email,
        source="admin",
    )
