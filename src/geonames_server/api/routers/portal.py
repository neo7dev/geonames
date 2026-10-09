"""Account pages for signed-in users: their profile, limits and API keys.

Authenticated with the Supabase Auth login token, not an API key.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.api.auth import Principal, generate_api_key, require_user
from geonames_server.api.dependencies import get_session
from geonames_server.api.schemas.portal import (
    ApiKeyResponse,
    CreateApiKeyRequest,
    CreatedApiKeyResponse,
    Limits,
    MeResponse,
)
from geonames_server.database.models.accounts import ApiKey, Profile

router = APIRouter(tags=["Portal"])


def _active_keys(user_id: uuid.UUID):
    return select(ApiKey).where(ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None))


async def _count_active_keys(session: AsyncSession, user_id: uuid.UUID) -> int:
    return await session.scalar(
        select(func.count()).select_from(_active_keys(user_id).subquery())
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
        _active_keys(principal.user_id).order_by(ApiKey.created_at.desc())
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
    created = CreatedApiKeyResponse.model_validate(
        {**ApiKeyResponse.model_validate(api_key).model_dump(), "key": key}
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
