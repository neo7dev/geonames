"""Disabled and not yet built datasets answer 503 instead of serving results."""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from geonames_server.api.app import app
from geonames_server.api.dependencies import _is_built as _is_built_unpatched
from geonames_server.api.dependencies import get_session
from geonames_server.database.models.dataset_metadata import DatasetMetaData

ENABLED = "geonames_server.api.dependencies.settings.enabled_datasets"
REPO = "geonames_server.api.routers.geonames.AsyncGeoNamesRepository"
BUILT = "geonames_server.api.dependencies._is_built"


@pytest.fixture(autouse=True)
def _built():
    with patch(BUILT, AsyncMock(return_value=True)) as built:
        yield built


@pytest_asyncio.fixture
async def http():
    async def _no_db():
        yield None

    app.dependency_overrides[get_session] = _no_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/cities500/", "/cities500/filter", "/cities500/123"])
async def test_disabled_dataset_returns_503_with_instructions(http, path):
    with patch(ENABLED, ["cities15000"]):
        resp = await http.get(path)

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "cities500" in detail and "disabled" in detail
    assert "ENABLED_DATASETS" in detail and "restart" in detail


@pytest.mark.asyncio
async def test_enabled_dataset_is_served(http):
    with patch(ENABLED, ["cities500"]), patch(REPO) as repo:
        repo.return_value.list = AsyncMock(return_value=[])
        resp = await http.get("/cities500/")

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_enabled_names_are_case_and_space_insensitive(http):
    with patch(ENABLED, [" Cities500 "]), patch(REPO) as repo:
        repo.return_value.list = AsyncMock(return_value=[])
        resp = await http.get("/cities500/")

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_other_datasets_unaffected_by_one_being_disabled(http):
    with patch(ENABLED, ["cities500"]), patch(REPO) as repo:
        repo.return_value.list = AsyncMock(return_value=[])
        assert (await http.get("/cities500/")).status_code == 200
        assert (await http.get("/allcountries/")).status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/cities500/", "/cities500/filter", "/cities500/123"])
async def test_unbuilt_dataset_returns_503_with_retry_after(http, _built, path):
    _built.return_value = False
    with patch(ENABLED, ["cities500"]), patch(REPO) as repo:
        resp = await http.get(path)

    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "60"
    assert "still being built" in resp.json()["detail"]
    repo.assert_not_called()


@pytest.mark.asyncio
async def test_built_dataset_is_checked_once(http, _built):
    with patch(ENABLED, ["cities500"]), patch(REPO) as repo:
        repo.return_value.list = AsyncMock(return_value=[])
        assert (await http.get("/cities500/")).status_code == 200
        assert (await http.get("/cities500/")).status_code == 200

    _built.assert_awaited_once()


@pytest.mark.asyncio
async def test_is_built_reads_last_modified(async_session, seed_metadata):
    await async_session.execute(
        update(DatasetMetaData)
        .where(DatasetMetaData.dataset == "cities500")
        .values(last_modified=None)
    )

    assert await _is_built_unpatched(async_session, "cities15000") is True
    assert await _is_built_unpatched(async_session, "cities500") is False
    assert await _is_built_unpatched(async_session, "cities5000") is False
