from collections.abc import AsyncGenerator, Awaitable, Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.config.config import settings
from geonames_server.database.models.dataset_metadata import DatasetMetaData
from geonames_server.database.session import asyncSessionLocal
from geonames_server.ingestion.registry import enabled_names


async def get_session() -> AsyncGenerator[AsyncSession]:
    async with asyncSessionLocal() as session:
        yield session


# Datasets seen with a completed build. A build is never undone while the server
# runs (FORCE_REBUILD drops tables before the API starts), so this only grows.
_built: set[str] = set()

# Seconds a client should wait before retrying a dataset that is still building.
BUILDING_RETRY_AFTER = 60


async def _is_built(session: AsyncSession, dataset: str) -> bool:
    built_at = await session.scalar(
        select(DatasetMetaData.last_modified).where(DatasetMetaData.dataset == dataset)
    )
    return built_at is not None


def require_dataset_available(dataset: str) -> Callable[..., Awaitable[None]]:
    """Build a dependency that rejects requests for a disabled or not yet built dataset.

    Until its first build finishes a dataset's table is empty, so answering 200
    with no results would look like a real empty result. A dataset that is
    being rebuilt because it is stale keeps serving its current data.
    """

    async def dependency(session: AsyncSession = Depends(get_session)) -> None:
        if dataset not in enabled_names(settings.enabled_datasets):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"Dataset '{dataset}' is disabled. Add it to ENABLED_DATASETS "
                    "and restart the server to enable it."
                ),
            )
        if dataset in _built:
            return
        if not await _is_built(session, dataset):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"Dataset '{dataset}' is still being built. Retry later; "
                    "/health lists the datasets that are not ready yet."
                ),
                headers={"Retry-After": str(BUILDING_RETRY_AFTER)},
            )
        _built.add(dataset)

    return dependency
