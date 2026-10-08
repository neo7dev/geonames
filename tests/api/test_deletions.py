import pytest


@pytest.mark.usefixtures("seed_deletions")
class TestListDeletions:
    @pytest.mark.asyncio
    async def test_list_deletions(self, client):
        response = await client.get("/deletions/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 3
        ids = {r["geonameid"] for r in data}
        assert {9999901, 9999902, 9999903} <= ids

    @pytest.mark.asyncio
    async def test_list_deletions_with_limit(self, client):
        response = await client.get("/deletions/", params={"limit": 1})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

    @pytest.mark.asyncio
    async def test_list_deletions_with_offset(self, client):
        all_resp = await client.get("/deletions/", params={"limit": 1000})
        total = len(all_resp.json())
        response = await client.get("/deletions/", params={"offset": 1})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == total - 1

    @pytest.mark.asyncio
    async def test_list_deletions_invalid_limit(self, client):
        response = await client.get("/deletions/", params={"limit": 0})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_list_deletions_limit_exceeds_max(self, client):
        response = await client.get("/deletions/", params={"limit": 1001})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_list_deletions_negative_offset(self, client):
        response = await client.get("/deletions/", params={"offset": -1})
        assert response.status_code == 422


@pytest.mark.usefixtures("seed_deletions")
class TestGetDeletionById:
    @pytest.mark.asyncio
    async def test_get_deletion_by_geonameid(self, client):
        response = await client.get("/deletions/9999901")
        assert response.status_code == 200
        data = response.json()
        assert data["geonameid"] == 9999901
        assert data["name"] == "OldTown"
        assert data["reason"] == "duplicate"
        assert data["deletion_date"] == "2025-03-01"

    @pytest.mark.asyncio
    async def test_get_deletion_not_found(self, client):
        response = await client.get("/deletions/999999999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


@pytest.mark.usefixtures("seed_deletions")
class TestFilterDeletions:
    @pytest.mark.asyncio
    async def test_filter_by_name(self, client):
        response = await client.get("/deletions/filter", params={"name": "OldTown"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "OldTown"

    @pytest.mark.asyncio
    async def test_filter_by_reason(self, client):
        response = await client.get("/deletions/filter", params={"reason": "duplicate"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["reason"] == "duplicate"

    @pytest.mark.asyncio
    async def test_filter_by_date(self, client):
        response = await client.get(
            "/deletions/filter", params={"deletion_date": "2025-03-01"}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2
        assert all(r["deletion_date"] == "2025-03-01" for r in data)
        ids = {r["geonameid"] for r in data}
        assert {9999901, 9999903} <= ids

    @pytest.mark.asyncio
    async def test_filter_by_geonameid(self, client):
        response = await client.get("/deletions/filter", params={"geonameid": 9999902})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "GhostCity"

    @pytest.mark.asyncio
    async def test_filter_with_ordering_desc(self, client):
        response = await client.get(
            "/deletions/filter", params={"order_by": "-geonameid"}
        )
        assert response.status_code == 200
        data = response.json()
        ids = [r["geonameid"] for r in data]
        assert ids == sorted(ids, reverse=True)

    @pytest.mark.asyncio
    async def test_filter_with_ordering_asc(self, client):
        response = await client.get(
            "/deletions/filter", params={"order_by": "geonameid"}
        )
        assert response.status_code == 200
        data = response.json()
        ids = [r["geonameid"] for r in data]
        assert ids == sorted(ids)

    @pytest.mark.asyncio
    async def test_filter_with_limit(self, client):
        response = await client.get(
            "/deletions/filter",
            params={"order_by": "geonameid", "limit": 2},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_filter_empty_result(self, client):
        response = await client.get(
            "/deletions/filter", params={"name": "NonExistentPlace"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data == []
