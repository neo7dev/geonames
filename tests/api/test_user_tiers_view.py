"""app.user_tiers: each user's tier, limits and key counts, for Studio."""

import pytest
from sqlalchemy import func, text, update

from geonames_server.database.models.accounts import ApiKey


async def _row(session, user_id):
    result = await session.execute(
        text("SELECT * FROM app.user_tiers WHERE user_id = :id"), {"id": user_id}
    )
    return result.mappings().one()


@pytest.mark.asyncio
async def test_shows_role_tier_and_limits(async_session, make_user):
    user_id = await make_user("dev", "view@example.com")

    row = await _row(async_session, user_id)

    assert {k: row[k] for k in ("email", "role", "tier", "is_admin")} == {
        "email": "view@example.com",
        "role": "dev",
        "tier": "pro",
        "is_admin": False,
    }
    assert (row["per_minute"], row["per_hour"], row["per_month"], row["max_keys"]) == (
        120,
        2500,
        500000,
        5,
    )
    assert (row["active_keys"], row["revoked_keys"]) == (0, 0)


@pytest.mark.asyncio
async def test_unlimited_tier_shows_nulls(async_session, make_user):
    row = await _row(async_session, await make_user("admin"))

    assert (row["tier"], row["is_admin"]) == ("max", True)
    assert (row["per_hour"], row["per_month"], row["max_keys"]) == (None, None, None)


@pytest.mark.asyncio
async def test_counts_active_and_revoked_keys(async_session, make_user, make_key):
    user_id = await make_user("tester")
    for name in ("a", "b", "c"):
        await make_key(user_id, name)
    await async_session.execute(
        update(ApiKey).where(ApiKey.name == "c").values(revoked_at=func.now())
    )
    await async_session.commit()

    row = await _row(async_session, user_id)

    assert (row["active_keys"], row["revoked_keys"]) == (2, 1)
