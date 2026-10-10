import os

import psycopg2
import pytest
from sqlalchemy import create_engine

from geonames_server.database.extensions import install_pg_trgm


@pytest.fixture(scope="session", autouse=True)
def _pg_trgm_in_the_test_database():
    """The geonames models' trigram indexes need pg_trgm, also where tests create
    tables from the models instead of running the migrations."""
    dsn = os.environ.get("TEST_PG_DSN")
    if not dsn:
        return
    engine = create_engine(
        "postgresql+psycopg2://", creator=lambda: psycopg2.connect(dsn)
    )
    try:
        with engine.begin() as conn:
            install_pg_trgm(conn)
    finally:
        engine.dispose()
