from collections.abc import AsyncGenerator, Callable

from fastapi import HTTPException, status

from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.config.config import settings
from geonames_server.database.session import asyncSessionLocal
from geonames_server.ingestion.registry import enabled_names


async def get_session() -> AsyncGenerator[AsyncSession]:
    async with asyncSessionLocal() as session:
        yield session


def require_dataset_enabled(dataset: str) -> Callable[[], None]:
    """Build a dependency that rejects requests for a disabled dataset."""

    def dependency() -> None:
        if dataset not in enabled_names(settings.enabled_datasets):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"Dataset '{dataset}' is disabled. Add it to ENABLED_DATASETS "
                    "and restart the server to enable it."
                ),
            )

    return dependency
