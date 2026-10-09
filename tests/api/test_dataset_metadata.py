import pytest


@pytest.mark.usefixtures("seed_metadata")
class TestListMetadata:
    @pytest.mark.asyncio
    async def test_list_metadata(self, client):
        response = await client.get("/metadata/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 3
        datasets = {r["dataset"] for r in data}
        assert {"cities15000", "allcountries", "cities500"} <= datasets

    @pytest.mark.asyncio
    async def test_list_metadata_with_limit(self, client):
        response = await client.get("/metadata/", params={"limit": 1})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

    @pytest.mark.asyncio
    async def test_list_metadata_with_offset(self, client):
        all_resp = await client.get("/metadata/", params={"limit": 1000})
        total = len(all_resp.json())
        response = await client.get("/metadata/", params={"offset": 1})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == total - 1

    @pytest.mark.asyncio
    async def test_list_metadata_invalid_limit(self, client):
        response = await client.get("/metadata/", params={"limit": 0})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_list_metadata_limit_exceeds_max(self, client):
        response = await client.get("/metadata/", params={"limit": 1001})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_list_metadata_negative_offset(self, client):
        response = await client.get("/metadata/", params={"offset": -1})
        assert response.status_code == 422


@pytest.mark.usefixtures("seed_metadata")
class TestGetMetadataByDataset:
    @pytest.mark.asyncio
    async def test_get_metadata_by_dataset(self, client):
        response = await client.get("/metadata/cities15000")
        assert response.status_code == 200
        data = response.json()
        assert data["dataset"] == "cities15000"
        assert data["out_of_sync"] is False
        assert data["etag"] == "abc123"
        assert data["txt_file"] == "cities15000.txt"

    @pytest.mark.asyncio
    async def test_get_metadata_not_found(self, client):
        response = await client.get("/metadata/nonexistent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


@pytest.mark.usefixtures("seed_metadata")
class TestGetOutOfSync:
    @pytest.mark.asyncio
    async def test_get_out_of_sync(self, client):
        response = await client.get("/metadata/out-of-sync")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["dataset"] == "allcountries"
        assert data[0]["out_of_sync"] is True


@pytest.mark.usefixtures("seed_metadata")
class TestGetNeedsRebuild:
    @pytest.mark.asyncio
    async def test_get_needs_rebuild(self, client):
        response = await client.get("/metadata/needs-rebuild")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["dataset"] == "allcountries"
        assert data[0]["needs_rebuild"] is True

    @pytest.mark.asyncio
    async def test_get_needs_rebuild_empty(self, client, async_session):
        """When no datasets need rebuild, return empty list."""
        from geonames_server.database.models.dataset_metadata import DatasetMetaData

        await async_session.execute(DatasetMetaData.__table__.delete())
        await async_session.commit()
        response = await client.get("/metadata/needs-rebuild")
        assert response.status_code == 200
        assert response.json() == []


@pytest.mark.usefixtures("seed_metadata")
class TestFilterMetadata:
    @pytest.mark.asyncio
    async def test_filter_by_dataset(self, client):
        response = await client.get(
            "/metadata/filter", params={"dataset": "cities15000"}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["dataset"] == "cities15000"

    @pytest.mark.asyncio
    async def test_filter_by_out_of_sync_true(self, client):
        response = await client.get("/metadata/filter", params={"out_of_sync": True})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["out_of_sync"] is True

    @pytest.mark.asyncio
    async def test_filter_by_out_of_sync_false(self, client):
        response = await client.get("/metadata/filter", params={"out_of_sync": False})
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2
        assert all(r["out_of_sync"] is False for r in data)
        datasets = {r["dataset"] for r in data}
        assert {"cities15000", "cities500"} <= datasets

    @pytest.mark.asyncio
    async def test_filter_by_content_length_range(self, client):
        # content_length was removed from the model; test filtering by etag instead
        response = await client.get(
            "/metadata/filter",
            params={"etag": "abc123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["dataset"] == "cities15000"

    @pytest.mark.asyncio
    async def test_filter_by_etag(self, client):
        response = await client.get("/metadata/filter", params={"etag": "abc123"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["etag"] == "abc123"

    @pytest.mark.asyncio
    async def test_filter_with_ordering_desc(self, client):
        response = await client.get("/metadata/filter", params={"order_by": "-dataset"})
        assert response.status_code == 200
        data = response.json()
        datasets = [r["dataset"] for r in data]
        assert datasets == sorted(datasets, reverse=True)

    @pytest.mark.asyncio
    async def test_filter_with_ordering_asc(self, client):
        response = await client.get("/metadata/filter", params={"order_by": "dataset"})
        assert response.status_code == 200
        data = response.json()
        datasets = [r["dataset"] for r in data]
        assert datasets == sorted(datasets)

    @pytest.mark.asyncio
    async def test_filter_with_limit(self, client):
        response = await client.get(
            "/metadata/filter",
            params={"order_by": "dataset", "limit": 2},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_filter_empty_result(self, client):
        response = await client.get(
            "/metadata/filter", params={"dataset": "nonexistent"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data == []
