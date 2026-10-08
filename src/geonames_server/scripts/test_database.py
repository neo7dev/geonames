from rich import pretty
from rich import print as rprint

from geonames_server.database.init_db import init_db
from geonames_server.database.models.cities15000 import Cities15000
from geonames_server.database.repository.filters.geonames_filter import GeoNamesFilter
from geonames_server.database.repository.sync.geoname_repository import (
    GeoNamesRepository,
)
from geonames_server.database.session import get_session


def main():
    pretty.install()
    init_db()

    # with get_session() as session:
    #     metadata = DatasetMetaData(
    #         dataset_name="test_dataset",
    #         out_of_sync=False,
    #         etag="test_etag",
    #         last_modified="2024-06-01",
    #     )

    #     session.add(metadata)

    with get_session() as session:
        repo = GeoNamesRepository(Cities15000, session)
        results = repo.search_by_name("Berlin")
        for result in results:
            rprint(
                f"Found: {result.name} in {result.country_code} with population {result.population}"
            )

        rprint("\nTesting population filter:")

        results = repo.filter(
            GeoNamesFilter(
                timezone="Europe/Berlin",
                limit=10,
                feature_code="PPLA",
                order_by="-population",
            )
        )
        for result in results:
            rprint(result.to_dict())


if __name__ == "__main__":
    main()
