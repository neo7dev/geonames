"""Integration tests for indexes.build_missing_indexes against a real PostgreSQL.

Set TEST_PG_DSN to a throwaway database; the cities500 table in it is dropped
and recreated.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import psycopg2
import pytest
from psycopg2.extensions import parse_dsn
from sqlalchemy import create_engine

from geonames_server.database.models.cities500 import Cities500
from geonames_server.ingestion import indexes, pipeline
from geonames_server.ingestion.indexes import build_missing_indexes
from geonames_server.ingestion.registry import DatasetInfo

DSN = os.environ.get("TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_PG_DSN not set")

TABLE = "cities500"
DATASET = DatasetInfo(
    name=TABLE, zip_file="cities500.zip", txt_file="cities500.txt", model=Cities500
)
MODEL_INDEXES = {i.name for i in Cities500.__table__.indexes}


@pytest.fixture
def conn():
    c = psycopg2.connect(DSN)
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {TABLE} CASCADE")
    Cities500.__table__.create(
        bind=create_engine(
            "postgresql+psycopg2://", creator=lambda: psycopg2.connect(DSN)
        )
    )
    with (
        patch.object(indexes, "_get_conn_params", return_value=parse_dsn(DSN)),
        patch.object(pipeline, "_get_conn_params", return_value=parse_dsn(DSN)),
    ):
        yield c
    with c.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {TABLE} CASCADE")
    c.close()


def _indexes(conn) -> dict[str, bool]:
    """Index name -> valid, for the table's indexes other than the primary key."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname, i.indisvalid FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE i.indrelid = to_regclass(%s) AND NOT i.indisprimary",
            (TABLE,),
        )
        return dict(cur.fetchall())


def _drop(conn, *names: str) -> None:
    with conn.cursor() as cur:
        for name in names:
            cur.execute(f'DROP INDEX "{name}"')


def test_builds_the_missing_indexes(conn):
    _drop(conn, "ix_cities500_name_trgm", "ix_cities500_population")

    assert build_missing_indexes([DATASET]) == []

    assert _indexes(conn) == dict.fromkeys(MODEL_INDEXES, True)


def test_replaces_an_invalid_index(conn):
    _drop(conn, "ix_cities500_population")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO cities500 (geonameid, name, asciiname, latitude, longitude, "
            "population) VALUES (1, 'a', 'a', 0, 0, 5), (2, 'b', 'b', 0, 0, 5)"
        )
        # A failed concurrent build leaves an invalid index behind
        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute(
                "CREATE UNIQUE INDEX CONCURRENTLY ix_cities500_population "
                "ON cities500 (population)"
            )
    assert _indexes(conn)["ix_cities500_population"] is False

    assert build_missing_indexes([DATASET]) == []

    assert _indexes(conn)["ix_cities500_population"] is True


def test_leaves_valid_indexes_alone(conn):
    before = _indexes(conn)

    with patch.object(indexes, "dataset_lock") as lock:
        assert build_missing_indexes([DATASET]) == []

    lock.assert_not_called()
    assert _indexes(conn) == before
