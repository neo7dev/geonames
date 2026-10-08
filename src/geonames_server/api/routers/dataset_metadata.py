from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.api.dependencies import get_session
from geonames_server.api.schemas.dataset_metadata import DatasetMetaDataResponse
from geonames_server.config.config import settings
from geonames_server.database.repository.async_.dataset_metadata_repository import (
    AsyncDatasetMetaDataRepository,
)
from geonames_server.database.repository.filters.dataset_metadata_filter import (
    DatasetMetaDataFilter,
)

DEFAULT_LIMIT = 100
MAX_LIMIT = settings.max_results

router = APIRouter(tags=["Dataset Metadata"])


@router.get("/", response_model=list[DatasetMetaDataResponse])
async def list_metadata(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
):
    repo = AsyncDatasetMetaDataRepository(session)
    return await repo.list(limit=limit, offset=offset)


@router.get("/out-of-sync", response_model=list[DatasetMetaDataResponse])
async def get_out_of_sync(
    session: AsyncSession = Depends(get_session),
):
    repo = AsyncDatasetMetaDataRepository(session)
    return await repo.get_out_of_sync()


@router.get("/needs-rebuild", response_model=list[DatasetMetaDataResponse])
async def get_needs_rebuild(
    session: AsyncSession = Depends(get_session),
):
    repo = AsyncDatasetMetaDataRepository(session)
    return await repo.get_needs_rebuild()


@router.get("/filter", response_model=list[DatasetMetaDataResponse])
async def filter_metadata(
    filters: DatasetMetaDataFilter = Depends(),
    session: AsyncSession = Depends(get_session),
):
    if filters.limit is None:
        filters.limit = DEFAULT_LIMIT
    filters.limit = min(filters.limit, MAX_LIMIT)
    repo = AsyncDatasetMetaDataRepository(session)
    return await repo.filter(filters)


@router.get("/{dataset}", response_model=DatasetMetaDataResponse)
async def get_metadata(
    dataset: str,
    session: AsyncSession = Depends(get_session),
):
    repo = AsyncDatasetMetaDataRepository(session)
    result = await repo.get_by_dataset(dataset)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset}' not found")
    return result
