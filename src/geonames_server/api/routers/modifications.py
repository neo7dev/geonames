from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.api.dependencies import get_session
from geonames_server.api.schemas.geoname import GeoNameResponse
from geonames_server.config.config import settings
from geonames_server.database.models.modifications import Modifications
from geonames_server.database.repository.async_.geoname_repository import (
    AsyncGeoNamesRepository,
)
from geonames_server.database.repository.filters.geonames_filter import GeoNamesFilter

router = APIRouter(tags=["Modifications"])

DEFAULT_LIMIT = 100
MAX_LIMIT = settings.max_results


@router.get("/", response_model=list[GeoNameResponse])
async def list_modifications(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
):
    repo = AsyncGeoNamesRepository(Modifications, session)
    return await repo.list(limit=limit, offset=offset)


@router.get("/filter", response_model=list[GeoNameResponse])
async def filter_modifications(
    filters: Annotated[GeoNamesFilter, Query()],
    session: AsyncSession = Depends(get_session),
):
    if filters.limit is None:
        filters.limit = DEFAULT_LIMIT
    filters.limit = min(filters.limit, MAX_LIMIT)
    if filters.order_by is None:
        filters.order_by = "-geonameid"
    repo = AsyncGeoNamesRepository(Modifications, session)
    return await repo.filter(filters)


@router.get("/{geonameid}", response_model=list[GeoNameResponse])
async def get_modifications_by_geonameid(
    geonameid: int,
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
):
    stmt = (
        select(Modifications)
        .where(Modifications.geonameid == geonameid)
        .offset(offset)
        .limit(limit)
    )
    result = await session.scalars(stmt)
    return list(result.all())


@router.get("/date/{modification_date}", response_model=list[GeoNameResponse])
async def get_modifications_by_date(
    modification_date: date,
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
):
    stmt = (
        select(Modifications)
        .where(Modifications.modification_date == modification_date)
        .offset(offset)
        .limit(limit)
    )
    result = await session.scalars(stmt)
    return list(result.all())
