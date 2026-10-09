from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.api.dependencies import get_session
from geonames_server.api.schemas.deletion import DeletionResponse
from geonames_server.config.config import settings
from geonames_server.database.repository.async_.deletion_repository import (
    AsyncDeletionRepository,
)
from geonames_server.database.repository.filters.deletion_filter import DeletionFilter

DEFAULT_LIMIT = 100
MAX_LIMIT = settings.max_results

router = APIRouter(tags=["Deletions"])


@router.get("/", response_model=list[DeletionResponse])
async def list_deletions(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
):
    repo = AsyncDeletionRepository(session)
    return await repo.list(limit=limit, offset=offset)


@router.get("/filter", response_model=list[DeletionResponse])
async def filter_deletions(
    filters: Annotated[DeletionFilter, Query()],
    session: AsyncSession = Depends(get_session),
):
    if filters.limit is None:
        filters.limit = DEFAULT_LIMIT
    filters.limit = min(filters.limit, MAX_LIMIT)
    repo = AsyncDeletionRepository(session)
    return await repo.filter(filters)


@router.get("/{geonameid}", response_model=DeletionResponse)
async def get_deletion(
    geonameid: int,
    session: AsyncSession = Depends(get_session),
):
    repo = AsyncDeletionRepository(session)
    result = await repo.get_by_geonameid(geonameid)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Deletion {geonameid} not found")
    return result
