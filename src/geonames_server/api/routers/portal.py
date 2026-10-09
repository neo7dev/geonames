"""Account pages for signed-in users: their profile, limits and API keys.

Authenticated with the Supabase Auth login token, not an API key.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import Date, cast, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.api import rate_limit
from geonames_server.api.auth import (
    Principal,
    forget_api_key,
    generate_api_key,
    require_user,
)
from geonames_server.api.dependencies import get_session
from geonames_server.api.rate_limit import (
    RateLimiter,
    Windows,
    flushed_counts,
    stored_usage,
)
from geonames_server.api.schemas.portal import (
    ApiKeyResponse,
    CreateApiKeyRequest,
    CreatedApiKeyResponse,
    CurrentUsage,
    DailyUsage,
    KeyUsage,
    Limits,
    MeResponse,
    UsageResponse,
    UsageWindow,
)
from geonames_server.database.models.accounts import ApiKey, ApiKeyUsage, Profile

router = APIRouter(tags=["Portal"])


def _is_active_key_of(user_id: uuid.UUID):
    return ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None)


async def _count_active_keys(session: AsyncSession, user_id: uuid.UUID) -> int:
    return await session.scalar(
        select(func.count(ApiKey.id)).where(*_is_active_key_of(user_id))
    )


@router.get("/me", response_model=MeResponse)
async def me(
    principal: Principal = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    return MeResponse(
        id=principal.user_id,
        email=principal.email,
        role=principal.role,
        tier=principal.tier,
        limits=Limits(
            per_minute=principal.per_minute,
            per_hour=principal.per_hour,
            per_month=principal.per_month,
            max_keys=principal.max_keys,
        ),
        active_keys=await _count_active_keys(session, principal.user_id),
    )


@router.get("/keys", response_model=list[ApiKeyResponse])
async def list_keys(
    principal: Principal = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    keys = await session.scalars(
        select(ApiKey)
        .where(*_is_active_key_of(principal.user_id))
        .order_by(ApiKey.created_at.desc())
    )
    return keys.all()


@router.post(
    "/keys", response_model=CreatedApiKeyResponse, status_code=status.HTTP_201_CREATED
)
async def create_key(
    body: CreateApiKeyRequest,
    principal: Principal = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    if principal.max_keys is not None:
        # Lock the profile so two concurrent requests cannot both pass the check.
        await session.execute(
            select(Profile.id).where(Profile.id == principal.user_id).with_for_update()
        )
        if await _count_active_keys(session, principal.user_id) >= principal.max_keys:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"The {principal.tier} tier allows {principal.max_keys} active "
                    "API key(s); revoke one to create another"
                ),
            )

    key, prefix, key_hash = generate_api_key()
    api_key = ApiKey(
        user_id=principal.user_id, name=body.name, prefix=prefix, key_hash=key_hash
    )
    session.add(api_key)
    # The INSERT returns the server-generated id and created_at
    await session.flush()
    created = CreatedApiKeyResponse(
        **ApiKeyResponse.model_validate(api_key).model_dump(), key=key
    )
    await session.commit()
    return created


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    key_id: uuid.UUID,
    principal: Principal = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    result = await session.execute(
        update(ApiKey)
        .where(
            ApiKey.id == key_id,
            ApiKey.user_id == principal.user_id,
            ApiKey.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
    )
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key '{key_id}' not found",
        )
    await session.commit()
    forget_api_key(key_id)


async def _current_counts(
    request: Request,
    session: AsyncSession,
    user_id: uuid.UUID,
    windows: Windows,
    now: float,
) -> tuple[list[int | None], bool]:
    limiter: RateLimiter | None = getattr(request.app.state, "rate_limiter", None)
    if limiter is not None:
        return await limiter.current_usage(user_id, windows, now)
    # No Redis configured: nothing is counted live, only what reached Postgres
    return flushed_counts(await stored_usage(session, user_id, windows)), False


async def _daily(
    session: AsyncSession, user_id: uuid.UUID, now: float, days: int
) -> list[DailyUsage]:
    today = datetime.fromtimestamp(now, timezone.utc).date()
    first = today - timedelta(days=days - 1)
    day = cast(func.timezone("UTC", ApiKeyUsage.hour), Date)
    rows = await session.execute(
        select(day, func.sum(ApiKeyUsage.requests))
        .join(ApiKey, ApiKey.id == ApiKeyUsage.key_id)
        .where(
            ApiKey.user_id == user_id,
            ApiKeyUsage.hour
            >= datetime(first.year, first.month, first.day, tzinfo=timezone.utc),
        )
        .group_by(day)
    )
    by_day = dict(rows.tuples().all())
    return [
        DailyUsage(date=d, requests=int(by_day.get(d, 0)))
        for d in (first + timedelta(days=n) for n in range(days))
    ]


async def _key_usage(
    session: AsyncSession, user_id: uuid.UUID, windows: Windows
) -> list[KeyUsage]:
    this_month = func.coalesce(func.sum(ApiKeyUsage.requests), 0)
    rows = await session.execute(
        select(ApiKey.id, ApiKey.name, ApiKey.prefix, ApiKey.revoked_at, this_month)
        .outerjoin(
            ApiKeyUsage,
            (ApiKeyUsage.key_id == ApiKey.id)
            & (ApiKeyUsage.hour >= windows.month_start),
        )
        .where(ApiKey.user_id == user_id)
        .group_by(ApiKey.id)
        .having(or_(ApiKey.revoked_at.is_(None), this_month > 0))
        .order_by(ApiKey.created_at.desc())
    )
    return [
        KeyUsage(
            id=key_id,
            name=name,
            prefix=prefix,
            revoked=revoked_at is not None,
            requests_this_month=int(requests),
        )
        for key_id, name, prefix, revoked_at, requests in rows.tuples()
    ]


@router.get("/usage", response_model=UsageResponse)
async def usage(
    request: Request,
    days: Annotated[int, Query(ge=1, le=90, description="Days of history")] = 30,
    principal: Principal = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Current minute, hour and month against the tier's limits, plus history.

    The current counts are live from Redis. The daily history and per-key
    totals come from Postgres, so they are up to USAGE_FLUSH_SECONDS behind.
    """
    now = rate_limit._now()
    windows = Windows.at(now)
    used, live = await _current_counts(
        request, session, principal.user_id, windows, now
    )
    limits = rate_limit.limits_of(principal)
    current = [
        UsageWindow(
            used=used[w],
            limit=limits[w],
            resets_at=datetime.fromtimestamp(windows.ends[w], timezone.utc),
        )
        for w in range(3)
    ]
    return UsageResponse(
        tier=principal.tier,
        live=live,
        current=CurrentUsage(minute=current[0], hour=current[1], month=current[2]),
        daily=await _daily(session, principal.user_id, now, days),
        keys=await _key_usage(session, principal.user_id, windows),
    )
