"""Who changed an account, for the account_events log.

The trigger on app.profiles writes the log and reads the actor from two
settings of the current transaction. Tag a transaction before changing a
profile in it; the tag ends with the transaction, so tag again after a commit.
Changes without a tag are logged as external (Supabase Auth or Studio).
"""

import uuid
from typing import Literal

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.database.models.accounts import AccountEvent

Source = Literal["user", "admin", "system"]


async def tag_actor(
    session: AsyncSession, actor_id: uuid.UUID | None, source: Source
) -> None:
    await session.execute(
        text(
            "SELECT set_config('app.actor_id', :actor, true), "
            "set_config('app.actor_source', :source, true)"
        ),
        {"actor": str(actor_id) if actor_id else "", "source": source},
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
) -> None:
    """Mark an account the API created as created, by an admin or the server.

    Supabase Auth creates the profile in its own transaction, which the
    trigger logs as the user's own signup.
    """
    await _relabel(
        session,
        user_id,
        "signed_up",
        event="created",
        actor_id=actor_id,
        actor_email=actor_email,
        source=source,
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
