from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.api.dependencies import get_session, require_dataset_available
from geonames_server.api.schemas.geoname import GeoNameResponse
from geonames_server.config.config import settings
from geonames_server.database.repository.async_.geoname_repository import (
    AsyncGeoNamesRepository,
)
from geonames_server.database.repository.filters.geonames_filter import GeoNamesFilter

DEFAULT_LIMIT = 100
MAX_LIMIT = settings.max_results


def create_geoname_router(model: type, tag: str, dataset: str) -> APIRouter:
    router = APIRouter(
        tags=[tag], dependencies=[Depends(require_dataset_available(dataset))]
    )

    @router.get("/", response_model=list[GeoNameResponse])
    async def list_geonames(
        session: AsyncSession = Depends(get_session),
        limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
        offset: int = Query(default=0, ge=0),
    ):
        repo = AsyncGeoNamesRepository(model, session)
        return await repo.list(limit=limit, offset=offset)

    @router.get("/filter", response_model=list[GeoNameResponse])
    async def filter_geonames(
        filters: Annotated[GeoNamesFilter, Query()],
        session: AsyncSession = Depends(get_session),
    ):
        if filters.limit is None:
            filters.limit = DEFAULT_LIMIT
        filters.limit = min(filters.limit, MAX_LIMIT)
        if filters.order_by is None:
            filters.order_by = "-geonameid"
        repo = AsyncGeoNamesRepository(model, session)
        return await repo.filter(filters)

    @router.get("/{geonameid}", response_model=GeoNameResponse)
    async def get_geoname(
        geonameid: int,
        session: AsyncSession = Depends(get_session),
    ):
        repo = AsyncGeoNamesRepository(model, session)
        result = await repo.get_by_id(geonameid)
        if result is None:
            raise HTTPException(
                status_code=404, detail=f"Geoname {geonameid} not found"
            )
        return result

    return router
