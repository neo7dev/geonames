from unittest.mock import patch

import pytest

from geonames_server.api.app import app
from geonames_server.ingestion.sync_tracker import (
    DatasetStatus,
    OverallStatus,
    SyncStep,
    clear,
    complete_sync,
    create_sync,
    set_datasets,
)


@pytest.fixture(autouse=True)
def _clean_tracker():
    clear()
    yield
    clear()


@pytest.fixture(autouse=True)
def _startup_done():
    app.state.dataset_startup = "done"
    yield
    del app.state.dataset_startup


class TestPostSync:
    @pytest.mark.asyncio
    async def test_post_sync_returns_202(self, client):
        with patch(
            "geonames_server.api.routers.sync.run_tracked_sync"
        ) as mock_run:
            response = await client.post("/sync/")
            assert response.status_code == 202
            data = response.json()
            assert "sync_id" in data
            assert data["message"] == "Sync started"
            # ASGI test transport executes background tasks inline before
            # returning, so the mock will have been called with the sync_id.
            mock_run.assert_called_once_with(data["sync_id"])

    @pytest.mark.asyncio
    async def test_post_sync_returns_409_when_already_running(self, client):
        # Pre-create a running sync
        create_sync()
        with patch("geonames_server.api.routers.sync.run_tracked_sync"):
            response = await client.post("/sync/")
            assert response.status_code == 409
            assert "already in progress" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("startup", ["pending", "running"])
    async def test_post_sync_returns_409_while_startup_prepares_datasets(
        self, client, startup
    ):
        app.state.dataset_startup = startup
        with patch("geonames_server.api.routers.sync.run_tracked_sync") as mock_run:
            response = await client.post("/sync/")
        assert response.status_code == 409
        assert "being prepared at startup" in response.json()["detail"]
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_post_sync_allowed_after_failed_startup(self, client):
        app.state.dataset_startup = "failed"
        with patch("geonames_server.api.routers.sync.run_tracked_sync"):
            response = await client.post("/sync/")
        assert response.status_code == 202


class TestGetSyncStatus:
    @pytest.mark.asyncio
    async def test_get_returns_200_with_status(self, client):
        sync_id = create_sync()
        set_datasets(
            sync_id,
            [
                DatasetStatus(name="cities500", out_of_sync=True),
                DatasetStatus(name="cities15000", out_of_sync=False),
            ],
        )

        response = await client.get(f"/sync/{sync_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["sync_id"] == sync_id
        assert data["status"] == OverallStatus.RUNNING.value
        assert data["current_step"] == SyncStep.STARTING.value
        assert data["started_at"] is not None
        assert data["completed_at"] is None
        assert data["error"] is None
        assert len(data["datasets"]) == 2
        assert data["datasets"][0]["name"] == "cities500"
        assert data["datasets"][0]["out_of_sync"] is True

    @pytest.mark.asyncio
    async def test_get_completed_sync(self, client):
        sync_id = create_sync()
        complete_sync(sync_id)
        response = await client.get(f"/sync/{sync_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == OverallStatus.COMPLETED.value
        assert data["completed_at"] is not None

    @pytest.mark.asyncio
    async def test_get_unknown_sync_returns_404(self, client):
        response = await client.get("/sync/nonexistent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]
