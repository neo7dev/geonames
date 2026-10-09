from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import update

from geonames_server.api.app import app
from geonames_server.api.dependencies import get_session
from geonames_server.api.routers.health import redis_status
from geonames_server.config.config import settings
from geonames_server.database.models.dataset_metadata import DatasetMetaData

ROUTER = "geonames_server.api.routers.health"


@pytest_asyncio.fixture
async def built_cities15000(async_session):
    """cities15000 built, cities5000 still waiting for its rebuild."""
    for name, needs_rebuild in (("cities15000", False), ("cities5000", True)):
        await async_session.execute(
            update(DatasetMetaData)
            .where(DatasetMetaData.dataset == name)
            .values(needs_rebuild=needs_rebuild)
        )


@pytest.fixture(autouse=True)
def _settings():
    with patch.object(settings, "enabled_datasets", ["cities15000"]):
        app.state.dataset_startup = "done"
        app.state.redis = None
        yield
    del app.state.redis


class TestHealth:
    @pytest.mark.asyncio
    async def test_ok_when_everything_is_ready(self, client, built_cities15000):
        response = await client.get("/health")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "database": "ok",
            "redis": "not_configured",
            "datasets": {"startup": "done", "ready": ["cities15000"], "pending": []},
        }

    @pytest.mark.asyncio
    async def test_degraded_but_healthy_while_datasets_build(
        self, client, built_cities15000
    ):
        app.state.dataset_startup = "running"
        with patch.object(settings, "enabled_datasets", ["cities15000", "cities5000"]):
            response = await client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"
        assert body["datasets"] == {
            "startup": "running",
            "ready": ["cities15000"],
            "pending": ["cities5000"],
        }

    @pytest.mark.asyncio
    async def test_degraded_when_startup_failed(self, client, built_cities15000):
        app.state.dataset_startup = "failed"

        response = await client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_degraded_when_redis_is_down(self, client, built_cities15000):
        with patch(f"{ROUTER}.redis_status", AsyncMock(return_value="error")):
            response = await client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
        assert response.json()["redis"] == "error"

    @pytest.mark.asyncio
    async def test_503_when_database_is_down(self, client):
        class _BrokenSession:
            async def execute(self, *args, **kwargs):
                raise ConnectionError("database down")

        async def _broken():
            yield _BrokenSession()

        app.dependency_overrides[get_session] = _broken

        response = await client.get("/health")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "error"
        assert body["database"] == "error"
        assert body["datasets"]["pending"] == ["cities15000"]


class TestRedisStatus:
    @pytest.mark.asyncio
    async def test_not_configured_without_client(self):
        assert await redis_status(None) == "not_configured"

    @pytest.mark.asyncio
    async def test_ok_when_ping_succeeds(self):
        assert await redis_status(AsyncMock()) == "ok"

    @pytest.mark.asyncio
    async def test_error_when_ping_fails(self):
        client = AsyncMock()
        client.ping.side_effect = ConnectionError("refused")
        assert await redis_status(client) == "error"

    @pytest.mark.asyncio
    async def test_health_pings_the_shared_client(self, client, built_cities15000):
        app.state.redis = AsyncMock()

        response = await client.get("/health")

        assert response.json()["redis"] == "ok"
        app.state.redis.ping.assert_awaited_once()
        app.state.redis.aclose.assert_not_called()
