from datetime import date

import pytest

from geonames_server.database.models.cities15000 import Cities15000
from geonames_server.database.repository.async_.geoname_repository import (
    AsyncGeoNamesRepository,
)
from geonames_server.database.repository.filters.geonames_filter import GeoNamesFilter

pytestmark = pytest.mark.asyncio(loop_scope="function")


class TestAsyncGeoNamesRepository:
    def _make_repo(self, session):
        return AsyncGeoNamesRepository(Cities15000, session)

    # -----------------------
    # Name Searches
    # -----------------------

    async def test_search_by_name(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.search_by_name("Berlin")
        assert len(results) >= 1
        assert any(c.name == "Berlin" for c in results)

    async def test_search_by_name_partial(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.search_by_name("on")  # London, etc.
        assert any(c.name == "London" for c in results)

    async def test_search_by_name_with_limit(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.search_by_name("", limit=2)
        assert len(results) == 2

    async def test_search_by_name_no_match(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.search_by_name("zzzznotacity")
        assert len(results) == 0

    async def test_search_by_asciiname(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.search_by_asciiname("Tokyo")
        assert len(results) >= 1
        assert any(c.asciiname == "Tokyo" for c in results)

    # -----------------------
    # Population Queries
    # -----------------------

    async def test_get_by_population_exact(self, async_session):
        repo = self._make_repo(async_session)
        # Berlin's real population
        berlin = await repo.get_by_id(2950159)
        results = await repo.get_by_population(berlin.population)
        assert any(c.geonameid == 2950159 for c in results)

    async def test_get_by_population_with_delta(self, async_session):
        repo = self._make_repo(async_session)
        # Berlin pop ~3.4M, search around 3.5M with delta 500K
        results = await repo.get_by_population(3500000, delta=500000)
        assert any(c.name == "Berlin" for c in results)

    async def test_get_min_population(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.get_min_population(8000000)
        names = {c.name for c in results}
        assert "New York City" in names
        assert "Tokyo" in names
        assert all(c.population >= 8000000 for c in results)

    async def test_get_max_population(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.get_max_population(2000000)
        assert any(c.name == "Munich" for c in results)
        assert all(c.population <= 2000000 for c in results)

    async def test_get_population_between(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.get_population_between(3000000, 9000000)
        names = {c.name for c in results}
        assert "Berlin" in names
        assert all(3000000 <= c.population <= 9000000 for c in results)

    # -----------------------
    # Timezone
    # -----------------------

    async def test_get_by_timezone(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.get_by_timezone("Europe/Berlin")
        names = {c.name for c in results}
        assert "Berlin" in names
        assert "Munich" in names
        assert all(c.timezone == "Europe/Berlin" for c in results)

    # -----------------------
    # Country
    # -----------------------

    async def test_get_by_country_code(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.get_by_country_code("de")
        assert len(results) >= 2
        assert all(c.country_code == "DE" for c in results)

    # -----------------------
    # Modification Date
    # -----------------------

    async def test_get_by_modification_date(self, async_session):
        repo = self._make_repo(async_session)
        # Berlin and Tokyo share mod_date 2025-07-22
        results = await repo.get_by_modification_date(date(2025, 7, 22))
        names = {c.name for c in results}
        assert "Berlin" in names
        assert "Tokyo" in names

    async def test_get_modified_after(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.get_modified_after(date(2026, 1, 1))
        names = {c.name for c in results}
        assert "London" in names
        assert "New York City" in names

    async def test_get_modified_between(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.get_modified_between(date(2025, 7, 1), date(2025, 8, 1))
        names = {c.name for c in results}
        assert "Berlin" in names
        assert "Tokyo" in names

    # -----------------------
    # Composable Filter
    # -----------------------

    async def test_filter_by_country(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(GeoNamesFilter(country_code="DE"))
        assert len(results) >= 2
        assert all(c.country_code == "DE" for c in results)

    async def test_filter_by_name(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(GeoNamesFilter(name="Berlin"))
        assert len(results) >= 1
        assert any(c.name == "Berlin" for c in results)

    async def test_filter_population_with_delta(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(GeoNamesFilter(population=9000000, delta=1000000))
        names = {c.name for c in results}
        assert "New York City" in names
        assert "Tokyo" in names

    async def test_filter_min_population(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(GeoNamesFilter(min_population=5000000))
        assert all(c.population >= 5000000 for c in results)

    async def test_filter_max_population(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(GeoNamesFilter(max_population=2000000))
        assert any(c.name == "Munich" for c in results)
        assert all(c.population <= 2000000 for c in results)

    async def test_filter_with_ordering(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(
            GeoNamesFilter(country_code="DE", order_by="-population")
        )
        assert results[0].name == "Berlin"
        assert results[1].name == "Hamburg"

    async def test_filter_with_limit_offset(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(GeoNamesFilter(limit=2, offset=1, order_by="name"))
        assert len(results) == 2

    async def test_filter_by_timezone(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(GeoNamesFilter(timezone="Asia/Tokyo"))
        assert len(results) >= 1
        assert all(c.timezone == "Asia/Tokyo" for c in results)

    async def test_filter_modified_after(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(GeoNamesFilter(modified_after=date(2026, 1, 1)))
        names = {c.name for c in results}
        assert "London" in names
        assert "New York City" in names

    async def test_filter_feature_code(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(GeoNamesFilter(feature_code="PPLC"))
        assert len(results) >= 3
        assert all(c.feature_code == "PPLC" for c in results)

    async def test_filter_no_conditions(self, async_session):
        repo = self._make_repo(async_session)
        results = await repo.filter(GeoNamesFilter())
        assert len(results) > 0
