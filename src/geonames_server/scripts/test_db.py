from rich.traceback import install

from geonames_server.database.init_db import init_db
from geonames_server.database.models.dataset_metadata import DatasetMetaData
from geonames_server.database.session import get_session

install(show_locals=True)


def test_db():
    init_db()
    with get_session() as session:
        metadata = DatasetMetaData(
            dataset="test_dataset2",
            last_updated="2024-06-01",
            out_of_sync=False,
            etag="test_etag",
            last_modified="2024-06-01",
        )
        session.add(metadata)
        print("Pending", session.new)


test_db()
