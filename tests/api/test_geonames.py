import pytest

from geonames_server.database.models.cities15000 import Cities15000


@pytest.mark.usefixtures("seed_geonames")
class TestListGeonames:
    @pytest.mark.asyncio
    async def test_list_geonames(self, client):
        response = await client.get("/cities15000/")
        assert response.status_code == 200
        data = response.json()
        # default limit is 100, so we just verify a successful list
        assert len(data) <= 100
        assert len(data) > 0

    @pytest.mark.asyncio
    async def test_list_geonames_with_limit(self, client):
        response = await client.get("/cities15000/", params={"limit": 2})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_list_geonames_with_offset(self, client):
        # Get results without offset
        base_resp = await client.get("/cities15000/", params={"limit": 5})
        base_data = base_resp.json()
        # Get results with offset=2 and same limit
        response = await client.get("/cities15000/", params={"limit": 5, "offset": 2})
        assert response.status_code == 200
        data = response.json()
        # The offset result should start where the 3rd record of the base was
        if len(base_data) >= 3:
            assert data[0]["geonameid"] == base_data[2]["geonameid"]

    @pytest.mark.asyncio
    async def test_list_geonames_invalid_limit_zero(self, client):
        response = await client.get("/cities15000/", params={"limit": 0})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_list_geonames_limit_exceeds_max(self, client):
        response = await client.get("/cities15000/", params={"limit": 1001})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_list_geonames_negative_offset(self, client):
        response = await client.get("/cities15000/", params={"offset": -1})
        assert response.status_code == 422


@pytest.mark.usefixtures("seed_geonames")
class TestGetGeonameById:
    @pytest.mark.asyncio
    async def test_get_geoname_by_id(self, client):
        response = await client.get("/cities15000/99000001")
        assert response.status_code == 200
        data = response.json()
        assert data["geonameid"] == 99000001
        assert data["name"] == "TestBerlin"
        assert data["country_code"] == "DE"
        assert data["latitude"] == pytest.approx(52.52437)
        assert data["longitude"] == pytest.approx(13.41053)
        assert data["population"] == 3677472
        assert data["timezone"] == "Europe/Berlin"

    @pytest.mark.asyncio
    async def test_get_geoname_not_found(self, client):
        response = await client.get("/cities15000/999999999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


@pytest.mark.usefixtures("seed_geonames")
class TestFilterGeonames:
    @pytest.mark.asyncio
    async def test_filter_by_country_code(self, client):
        response = await client.get(
            "/cities15000/filter", params={"country_code": "DE"}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2
        assert all(r["country_code"] == "DE" for r in data)

    @pytest.mark.asyncio
    async def test_filter_by_name(self, client):
        response = await client.get(
            "/cities15000/filter", params={"name": "TestBerlin"}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(r["geonameid"] == 99000001 for r in data)

    @pytest.mark.asyncio
    async def test_filter_by_population_range(self, client):
        # TestBerlin (3.6M), TestTokyo (8.3M), TestLondon (7.5M), TestMunich (1.2M)
        response = await client.get(
            "/cities15000/filter",
            params={"min_population": 3000000, "max_population": 9000000},
        )
        assert response.status_code == 200
        data = response.json()
        for r in data:
            assert 3000000 <= r["population"] <= 9000000
        ids = {r["geonameid"] for r in data}
        assert {99000001, 99000002, 99000003} <= ids

    @pytest.mark.asyncio
    async def test_filter_with_ordering_desc(self, client):
        response = await client.get(
            "/cities15000/filter", params={"order_by": "-population"}
        )
        assert response.status_code == 200
        data = response.json()
        populations = [r["population"] for r in data]
        assert populations == sorted(populations, reverse=True)

    @pytest.mark.asyncio
    async def test_filter_with_ordering_asc(self, client):
        response = await client.get(
            "/cities15000/filter", params={"order_by": "population"}
        )
        assert response.status_code == 200
        data = response.json()
        populations = [r["population"] for r in data]
        assert populations == sorted(populations)

    @pytest.mark.asyncio
    async def test_filter_with_limit_offset(self, client):
        response = await client.get(
            "/cities15000/filter",
            params={"order_by": "geonameid", "limit": 2, "offset": 1},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_filter_empty_result(self, client):
        response = await client.get(
            "/cities15000/filter", params={"country_code": "ZZ"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data == []

    @pytest.mark.asyncio
    async def test_filter_by_feature_class(self, client):
        response = await client.get(
            "/cities15000/filter", params={"feature_class": "P"}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 4
        assert all(r["feature_class"] == "P" for r in data)

    @pytest.mark.asyncio
    async def test_filter_by_timezone(self, client):
        response = await client.get(
            "/cities15000/filter", params={"timezone": "Europe/Berlin"}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2
        assert all(r["timezone"] == "Europe/Berlin" for r in data)

    @pytest.mark.asyncio
    async def test_filter_population_with_delta(self, client):
        # TestBerlin population 3677472, delta 100000 → range [3577472, 3777472]
        response = await client.get(
            "/cities15000/filter",
            params={"population": 3677472, "delta": 100000},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(r["geonameid"] == 99000001 for r in data)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "param, field",
        [
            ("latitude", "latitude"),
            ("longitude", "longitude"),
            ("elevation", "elevation"),
            ("dem", "dem"),
            ("max_population", "population"),
        ],
    )
    async def test_filter_by_zero(self, client, async_session, param, field):
        # Zero is a value (the equator, sea level), not a missing filter
        async_session.add(
            Cities15000(
                geonameid=99000005,
                name="TestNullIsland",
                asciiname="TestNullIsland",
                latitude=0,
                longitude=0,
                population=0,
                elevation=0,
                dem=0,
            )
        )
        await async_session.commit()
        response = await client.get("/cities15000/filter", params={param: 0})
        assert response.status_code == 200
        data = response.json()
        assert any(r["geonameid"] == 99000005 for r in data)
        assert all(r[field] == 0 for r in data)
