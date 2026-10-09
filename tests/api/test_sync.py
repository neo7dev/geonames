from unittest.mock import patch

import pytest
from sqlalchemy import update

from geonames_server.api.app import app
from geonames_server.api.dependencies import get_session
from geonames_server.database.models.accounts import Role
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
        with patch("geonames_server.api.routers.sync.run_tracked_sync") as mock_run:
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


class TestAdminOnly:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("role", ["free", "tester", "dev", "sr_dev"])
    async def test_non_admin_cannot_start_or_inspect_a_sync(
        self, auth_client, make_user, make_key, role
    ):
        key = await make_key(await make_user(role))
        sync_id = create_sync()
        headers = {"X-API-Key": key}

        with patch("geonames_server.api.routers.sync.run_tracked_sync") as mock_run:
            started = await auth_client.post("/sync/", headers=headers)
        status = await auth_client.get(f"/sync/{sync_id}", headers=headers)

        assert started.status_code == 403
        assert status.status_code == 403
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_admin_can_start_and_inspect_a_sync(
        self, auth_client, make_user, make_key
    ):
        headers = {"X-API-Key": await make_key(await make_user("admin"))}

        with patch("geonames_server.api.routers.sync.run_tracked_sync"):
            started = await auth_client.post("/sync/", headers=headers)
        status = await auth_client.get(
            f"/sync/{started.json()['sync_id']}", headers=headers
        )

        assert started.status_code == 202
        assert status.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_flag_on_a_role_grants_access(
        self, auth_client, async_session, make_user, make_key
    ):
        headers = {"X-API-Key": await make_key(await make_user("dev"))}
        await async_session.execute(
            update(Role).where(Role.name == "dev").values(is_admin=True)
        )
        await async_session.commit()

        with patch("geonames_server.api.routers.sync.run_tracked_sync"):
            resp = await auth_client.post("/sync/", headers=headers)

        assert resp.status_code == 202

    @pytest.mark.asyncio
    async def test_sync_needs_a_key(self, auth_client):
        assert (await auth_client.post("/sync/")).status_code == 401

    @pytest.mark.asyncio
    async def test_admin_check_releases_its_session_before_the_sync_runs(
        self, auth_client, async_session, make_user, make_key
    ):
        # The sync runs as a background task after the response; a session still
        # open then would hold a connection, mid-transaction, for the whole sync.
        headers = {"X-API-Key": await make_key(await make_user("admin"))}
        open_sessions = 0

        async def _counted_session():
            nonlocal open_sessions
            open_sessions += 1
            try:
                yield async_session
            finally:
                open_sessions -= 1

        app.dependency_overrides[get_session] = _counted_session
        open_during_sync = []
        with patch(
            "geonames_server.api.routers.sync.run_tracked_sync",
            side_effect=lambda _: open_during_sync.append(open_sessions),
        ):
            resp = await auth_client.post("/sync/", headers=headers)

        assert resp.status_code == 202
        assert open_during_sync == [0]
