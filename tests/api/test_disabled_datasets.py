"""Disabled datasets reject requests with a message on how to enable them."""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from geonames_server.api.app import app
from geonames_server.api.dependencies import get_session

ENABLED = "geonames_server.api.dependencies.settings.enabled_datasets"
REPO = "geonames_server.api.routers.geonames.AsyncGeoNamesRepository"


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
