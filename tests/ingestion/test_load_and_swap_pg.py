"""Integration tests for pipeline._load_and_swap against a real PostgreSQL.

Set TEST_PG_DSN (e.g. "host=localhost port=5432 user=postgres password=x dbname=scratch")
to a throwaway database; the cities500 table in it is dropped and recreated.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import psycopg2
import pytest
from psycopg2.extensions import parse_dsn
from sqlalchemy import create_engine

from geonames_server.database.models.cities500 import Cities500
from geonames_server.ingestion import pipeline
from geonames_server.ingestion.pipeline import RowCountError, _load_and_swap

DSN = os.environ.get("TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_PG_DSN not set")

TABLE = "cities500"


def _row(i: int, name: str = "City") -> str:
    # geonameid name asciiname alternatenames lat lon fc fcode cc cc2 a1-a4 pop elev dem tz moddate
    return "\t".join(
        [str(i), f"{name}{i}", f"{name}{i}", "", "1.5", "2.5", "P", "PPL", "US", "",
         "", "", "", "", "1000", "", "10", "UTC", "2026-01-01"]
    )


@pytest.fixture
def conn():
    c = psycopg2.connect(DSN)
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}, {TABLE}_new, {TABLE}_old CASCADE")
    Cities500.__table__.create(
        bind=create_engine("postgresql+psycopg2://", creator=lambda: psycopg2.connect(DSN))
    )
    yield c
    with c.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}, {TABLE}_new, {TABLE}_old CASCADE")
    c.close()


def _run(path):
    with patch.object(pipeline, "_get_conn_params", return_value=parse_dsn(DSN)):
        _load_and_swap(Cities500, TABLE, path)


def _scalar(conn, query):
    with conn.cursor() as cur:
        cur.execute(query)
        return cur.fetchone()[0]


def _file(tmp_path, lines):
    p = tmp_path / "cities500.txt"
    p.write_text("".join(f"{line}\n" for line in lines))
    return p


def test_replaces_contents_and_keeps_schema(conn, tmp_path):
    _run(_file(tmp_path, [_row(1, "Old"), _row(2, "Old")]))
    _run(_file(tmp_path, [_row(2, "New"), _row(3, "New")]))

    assert _names(conn) == ["New2", "New3"]
    assert set(_index_names(conn)) == {"cities500_pkey", "ix_cities500_geonameid"}
    assert _scalar(conn, "SELECT to_regclass('cities500_new') IS NULL")
    assert _scalar(conn, "SELECT to_regclass('cities500_old') IS NULL")


def test_swapped_table_is_analysed(conn, tmp_path):
    _run(_file(tmp_path, [_row(i) for i in range(1, 6)]))
    assert _scalar(conn, "SELECT reltuples FROM pg_class WHERE oid = 'cities500'::regclass") == 5


def test_duplicate_keys_are_dropped(conn, tmp_path):
    _run(_file(tmp_path, [_row(1), _row(1), _row(2)]))
    assert _scalar(conn, f"SELECT array_agg(geonameid) FROM {TABLE}") == [2]


def test_empty_load_is_refused_and_live_data_kept(conn, tmp_path):
    _run(_file(tmp_path, [_row(1), _row(2)]))
    with pytest.raises(RowCountError):
        _run(_file(tmp_path, [_row(i) for i in range(0)]))
    assert _scalar(conn, f"SELECT COUNT(*) FROM {TABLE}") == 2


def test_much_smaller_load_is_refused(conn, tmp_path):
    _run(_file(tmp_path, [_row(i) for i in range(1, 21)]))
    with pytest.raises(RowCountError):
        _run(_file(tmp_path, [_row(1)]))
    assert _scalar(conn, f"SELECT COUNT(*) FROM {TABLE}") == 20


def test_load_shrunk_by_deduplication_is_refused(conn, tmp_path):
    _run(_file(tmp_path, [_row(i) for i in range(1, 11)]))
    # 20 rows pass a pre-dedup count, but only one unique key survives
    with pytest.raises(RowCountError):
        _run(_file(tmp_path, [_row(i) for i in range(1, 11)] * 2 + [_row(99)]))
    assert _scalar(conn, f"SELECT COUNT(*) FROM {TABLE}") == 10


def test_bad_row_rolls_back_everything(conn, tmp_path):
    _run(_file(tmp_path, [_row(1, "Keep")]))
    with pytest.raises(psycopg2.Error):
        _run(_file(tmp_path, ["not\ta\tvalid\trow"]))
    assert _names(conn) == ["Keep1"]
    assert _scalar(conn, "SELECT to_regclass('cities500_new') IS NULL")


def test_swap_times_out_behind_open_reader_and_rolls_back(conn, tmp_path):
    _run(_file(tmp_path, [_row(1, "Old")]))
    # the load proceeds alongside an open reader; only the swap needs the lock
    reader = psycopg2.connect(DSN)
    with reader.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {TABLE}")  # holds ACCESS SHARE until commit
        with patch.object(pipeline, "SWAP_LOCK_TIMEOUT", "1s"):
            with pytest.raises(psycopg2.errors.LockNotAvailable):
                _run(_file(tmp_path, [_row(2, "New")]))
    reader.rollback()
    reader.close()
    # lock timeout rolled the swap back; table untouched
    assert _names(conn) == ["Old1"]


def _names(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT name FROM {TABLE} ORDER BY geonameid")
        return [r[0] for r in cur.fetchall()]


def _index_names(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT indexname FROM pg_indexes WHERE tablename = '{TABLE}'")
        return [r[0] for r in cur.fetchall()]


def test_sequence_ownership_survives_swap(conn, tmp_path):
    _run(_file(tmp_path, [_row(1)]))
    _run(_file(tmp_path, [_row(2)]))
    owned = _scalar(
        conn,
        "SELECT COUNT(*) FROM pg_depend d JOIN pg_class s ON s.oid = d.objid "
        "WHERE s.relkind = 'S' AND d.deptype = 'a' AND d.refobjid = 'cities500'::regclass",
    )
    default = _scalar(
        conn,
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_name = 'cities500' AND column_name = 'geonameid'",
    )
    assert owned == 1 and "nextval" in default


def test_dataset_lock_excludes_other_sessions(conn):
    key = (pipeline._DATASET_LOCK_NAMESPACE, "cities500")
    try_lock = "SELECT pg_try_advisory_lock(%s, hashtext(%s))"
    with patch.object(pipeline, "_get_conn_params", return_value=parse_dsn(DSN)):
        with pipeline.dataset_lock("cities500"):
            with conn.cursor() as cur:
                cur.execute(try_lock, key)
                assert cur.fetchone()[0] is False
        with conn.cursor() as cur:
            cur.execute(try_lock, key)
            assert cur.fetchone()[0] is True
            cur.execute("SELECT pg_advisory_unlock(%s, hashtext(%s))", key)
