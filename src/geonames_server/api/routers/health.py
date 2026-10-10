from fastapi import APIRouter, Depends, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.api.dependencies import get_session
from geonames_server.api.schemas.health import DatasetsHealth, HealthResponse
from geonames_server.config.config import settings
from geonames_server.database.models.dataset_metadata import DatasetMetaData
from geonames_server.ingestion.registry import enabled_names
from geonames_server.log.mylogger import get_logger

log = get_logger()

router = APIRouter(tags=["Health"])


async def redis_status(client: Redis | None) -> str:
    if client is None:
        return "not_configured"
    try:
        await client.ping()
        return "ok"
    except Exception as e:
        log.warning(f"Redis health check failed: {e}")
        return "error"


@router.get("", response_model=HealthResponse)
async def health(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    """Liveness and readiness. Returns 503 only when the database is unreachable,
    so the container stays healthy while datasets build in the background."""
    enabled = sorted(enabled_names(settings.enabled_datasets))
    startup = getattr(request.app.state, "dataset_startup", "pending")

    try:
        # Reading the metadata also proves the database is reachable.
        result = await session.execute(
            select(DatasetMetaData.dataset).where(
                DatasetMetaData.dataset.in_(enabled),
                DatasetMetaData.needs_rebuild.is_(False),
            )
        )
        built = set(result.scalars())
        database = "ok"
    except Exception as e:
        log.warning(f"Database health check failed: {e}")
        built = set()
        database = "error"

    redis = await redis_status(getattr(request.app.state, "redis", None))
    ready = [name for name in enabled if name in built]
    pending = [name for name in enabled if name not in built]

    if database == "error":
        overall = "error"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif redis == "error" or startup == "failed" or pending:
        overall = "degraded"
    else:
        overall = "ok"

    return HealthResponse(
        status=overall,
        version=settings.app_version,
        database=database,
        redis=redis,
        datasets=DatasetsHealth(startup=startup, ready=ready, pending=pending),
    )
