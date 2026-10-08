import pytest


@pytest.mark.usefixtures("seed_modifications")
class TestListModifications:
    @pytest.mark.asyncio
    async def test_list_modifications(self, client):
        response = await client.get("/modifications/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 3
        ids = {r["geonameid"] for r in data}
        assert {88000001, 88000002, 88000003} <= ids

    @pytest.mark.asyncio
    async def test_list_modifications_with_limit(self, client):
        response = await client.get("/modifications/", params={"limit": 2})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_list_modifications_with_offset(self, client):
        all_resp = await client.get("/modifications/", params={"limit": 1000})
        total = len(all_resp.json())
        response = await client.get("/modifications/", params={"offset": 1, "limit": 1000})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == total - 1

    @pytest.mark.asyncio
    async def test_list_modifications_invalid_limit_zero(self, client):
        response = await client.get("/modifications/", params={"limit": 0})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_list_modifications_limit_exceeds_max(self, client):
        response = await client.get("/modifications/", params={"limit": 1001})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_list_modifications_negative_offset(self, client):
        response = await client.get("/modifications/", params={"offset": -1})
        assert response.status_code == 422


@pytest.mark.usefixtures("seed_modifications")
class TestGetModificationsByGeonameid:
    @pytest.mark.asyncio
    async def test_get_by_geonameid(self, client):
        response = await client.get("/modifications/88000001")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["geonameid"] == 88000001
        assert data[0]["name"] == "ModifiedBerlin"
        assert data[0]["country_code"] == "DE"

    @pytest.mark.asyncio
    async def test_get_by_geonameid_not_found_returns_empty(self, client):
        # Unlike other endpoints, modifications returns empty list (no 404)
        response = await client.get("/modifications/999999999")
        assert response.status_code == 200
        data = response.json()
        assert data == []

    @pytest.mark.asyncio
    async def test_get_by_geonameid_with_limit(self, client):
        response = await client.get(
            "/modifications/88000001", params={"limit": 1}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) <= 1

    @pytest.mark.asyncio
    async def test_get_by_geonameid_response_fields(self, client):
        response = await client.get("/modifications/88000002")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        record = data[0]
        assert record["geonameid"] == 88000002
        assert record["name"] == "ModifiedTokyo"
        assert record["country_code"] == "JP"
        assert record["latitude"] == pytest.approx(35.68)
        assert record["longitude"] == pytest.approx(139.69)
        assert record["population"] == 8400000
        assert record["timezone"] == "Asia/Tokyo"
        assert record["modification_date"] == "2025-03-08"


@pytest.mark.usefixtures("seed_modifications")
class TestGetModificationsByDate:
    @pytest.mark.asyncio
    async def test_get_by_date(self, client):
        response = await client.get("/modifications/date/2025-03-08")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        ids = {r["geonameid"] for r in data}
        assert ids == {88000001, 88000002}

    @pytest.mark.asyncio
    async def test_get_by_date_single_result(self, client):
        response = await client.get("/modifications/date/2025-04-01")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["geonameid"] == 88000003
        assert data[0]["name"] == "ModifiedParis"

    @pytest.mark.asyncio
    async def test_get_by_date_no_results(self, client):
        response = await client.get("/modifications/date/2020-01-01")
        assert response.status_code == 200
        data = response.json()
        assert data == []

    @pytest.mark.asyncio
    async def test_get_by_date_with_limit(self, client):
        response = await client.get(
            "/modifications/date/2025-03-08", params={"limit": 1}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

    @pytest.mark.asyncio
    async def test_get_by_date_with_offset(self, client):
        response = await client.get(
            "/modifications/date/2025-03-08", params={"offset": 1}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

    @pytest.mark.asyncio
    async def test_get_by_date_invalid_format(self, client):
        response = await client.get("/modifications/date/not-a-date")
        assert response.status_code == 422


@pytest.mark.usefixtures("seed_modifications")
class TestFilterModifications:
    @pytest.mark.asyncio
    async def test_filter_by_country_code(self, client):
        response = await client.get(
            "/modifications/filter", params={"country_code": "DE"}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert all(r["country_code"] == "DE" for r in data)

    @pytest.mark.asyncio
    async def test_filter_by_name(self, client):
        response = await client.get(
            "/modifications/filter", params={"name": "ModifiedParis"}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert data[0]["name"] == "ModifiedParis"

    @pytest.mark.asyncio
    async def test_filter_by_population_range(self, client):
        response = await client.get(
            "/modifications/filter",
            params={"min_population": 3000000, "max_population": 9000000},
        )
        assert response.status_code == 200
        data = response.json()
        for r in data:
            assert 3000000 <= r["population"] <= 9000000
        ids = {r["geonameid"] for r in data}
        assert {88000001, 88000002} <= ids

    @pytest.mark.asyncio
    async def test_filter_with_ordering_desc(self, client):
        response = await client.get(
            "/modifications/filter", params={"order_by": "-population"}
        )
        assert response.status_code == 200
        data = response.json()
        populations = [r["population"] for r in data]
        assert populations == sorted(populations, reverse=True)

    @pytest.mark.asyncio
    async def test_filter_with_ordering_asc(self, client):
        response = await client.get(
            "/modifications/filter", params={"order_by": "population"}
        )
        assert response.status_code == 200
        data = response.json()
        populations = [r["population"] for r in data]
        assert populations == sorted(populations)

    @pytest.mark.asyncio
    async def test_filter_with_limit(self, client):
        response = await client.get(
            "/modifications/filter",
            params={"order_by": "geonameid", "limit": 2},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_filter_empty_result(self, client):
        response = await client.get(
            "/modifications/filter", params={"country_code": "ZZ"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data == []

    @pytest.mark.asyncio
    async def test_filter_by_timezone(self, client):
        response = await client.get(
            "/modifications/filter", params={"timezone": "Europe/Berlin"}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert all(r["timezone"] == "Europe/Berlin" for r in data)

    @pytest.mark.asyncio
    async def test_filter_by_feature_class(self, client):
        response = await client.get(
            "/modifications/filter", params={"feature_class": "P"}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 3
        assert all(r["feature_class"] == "P" for r in data)
