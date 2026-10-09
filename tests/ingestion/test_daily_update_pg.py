"""Integration tests for the daily update pipeline against a real PostgreSQL.

Set TEST_PG_DSN to a throwaway database (see test_load_and_swap_pg.py). The
allcountries, cities5000, modifications, deletions and dataset_metadata tables
in it are dropped and recreated. Only the HTTP download and "yesterday" are
faked; staging, upserts, deletes and the metadata event listeners are real.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import psycopg2
import pytest
from psycopg2.extensions import parse_dsn
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geonames_server.database.models.allcountries import AllCountries
from geonames_server.database.models.cities5000 import Cities5000
from geonames_server.database.models.dataset_metadata import DatasetMetaData
from geonames_server.database.models.deletions import Deletions
from geonames_server.database.models.modifications import Modifications
from geonames_server.ingestion import daily_update, pipeline
from geonames_server.ingestion.daily_update import MAX_CATCHUP_DAYS, apply_daily_updates
from geonames_server.ingestion.registry import ALL_DATASETS

DSN = os.environ.get("TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_PG_DSN not set")

Y = date(2026, 3, 18)  # "yesterday" for every run
# Local sync time of a dataset that was last synced before the days under test.
SYNCED_AT = datetime(2026, 3, 15, 1, 0, tzinfo=timezone.utc)

TABLES = [
    AllCountries.__table__,
    Cities5000.__table__,
    Modifications.__table__,
    Deletions.__table__,
    DatasetMetaData.__table__,
]
ALLCOUNTRIES = ALL_DATASETS["allcountries"]
CITIES5000 = ALL_DATASETS["cities5000"]


def _row(geonameid: int, name: str, day: date, population: int = 0) -> str:
    # geonameid name asciiname alternatenames lat lon fc fcode cc cc2 a1-a4 pop elev dem tz moddate
    return "\t".join(
        [
            str(geonameid),
            name,
            name,
            "",
            "1.5",
            "2.5",
            "P",
            "PPL",
            "US",
            "",
            "",
            "",
            "",
            "",
            str(population),
            "",
            "10",
            "UTC",
            day.isoformat(),
        ]
    )


@pytest.fixture
def engine():
    eng = create_engine("postgresql+psycopg2://", creator=lambda: psycopg2.connect(DSN))
    for table in TABLES:
        table.drop(eng, checkfirst=True)
    for table in TABLES:
        table.create(eng)
    yield eng
    for table in TABLES:
        table.drop(eng, checkfirst=True)
    eng.dispose()


class Remote:
    """Fake GeoNames server: daily files registered per day, served from tmp_path."""

    def __init__(self, tmp_path):
        self.dir = tmp_path
        self.files: dict[str, object] = {}

    def publish(self, day: date, modifications=(), deletes=None):
        """Publish *day*'s files. ``deletes=None`` leaves the deletes file unpublished."""
        self._write(f"modifications-{day.isoformat()}.txt", modifications)
        if deletes is not None:
            self._write(
                f"deletes-{day.isoformat()}.txt",
                [f"{gid}\tGone{gid}\tduplicate" for gid in deletes],
            )

    def _write(self, name, lines):
        path = self.dir / name
        path.write_text("".join(f"{line}\n" for line in lines))
        self.files[name] = path

    def download(self, _client, file_name):
        return self.files.get(file_name)


@pytest.fixture
def remote(engine, tmp_path):
    """Point the pipeline at the scratch DB and the fake remote."""
    conn_params = parse_dsn(DSN)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def session_scope():
        session = Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    fake = Remote(tmp_path)
    with (
        patch.object(daily_update, "_get_conn_params", return_value=conn_params),
        patch.object(pipeline, "_get_conn_params", return_value=conn_params),
        patch.object(daily_update, "get_session", session_scope),
        patch.object(daily_update, "_yesterday", return_value=Y),
        patch.object(daily_update, "RemoteFileClient"),
        patch.object(daily_update, "_download_daily_file", side_effect=fake.download),
    ):
        fake.session = session_scope
        yield fake


def _seed_rows(engine, model, rows):
    with engine.begin() as conn:
        conn.execute(model.__table__.insert(), rows)


def _geo(geonameid: int, name: str, population: int = 0) -> dict:
    return {
        "geonameid": geonameid,
        "name": name,
        "asciiname": name,
        "latitude": 1.5,
        "longitude": 2.5,
        "population": population,
        "modification_date": date(2026, 1, 1),
    }


def _seed_metadata(remote, ds, last_applied, last_modified=SYNCED_AT):
    """An out-of-sync dataset whose remote dump is newer than the local sync."""
    with remote.session() as s:
        s.add(
            DatasetMetaData(
                dataset=ds.name,
                zip_file=ds.zip_file,
                txt_file=ds.txt_file,
                etag='"old"',
                remote_etag='"new"',
                last_modified=last_modified,
                remote_last_modified=datetime(2026, 3, 18, 3, 0, tzinfo=timezone.utc),
                last_applied_date=last_applied,
            )
        )


def _metadata(remote, ds) -> dict:
    with remote.session() as s:
        record = s.get(DatasetMetaData, ds.name)
        return {
            "out_of_sync": record.out_of_sync,
            "needs_rebuild": record.needs_rebuild,
            "last_modified": record.last_modified,
            "last_applied_date": record.last_applied_date,
        }


def _names(engine, model) -> dict[int, str]:
    with engine.connect() as conn:
        table = model.__table__
        rows = conn.execute(table.select().order_by(table.c.geonameid))
        return {r.geonameid: r.name for r in rows}


# ---------------------------------------------------------------------------
# Rows actually change
# ---------------------------------------------------------------------------


def test_day_upserts_modified_rows_and_deletes_deleted_rows(engine, remote):
    _seed_rows(
        engine, AllCountries, [_geo(1, "Old1"), _geo(2, "Keep2"), _geo(3, "Doomed3")]
    )
    _seed_metadata(remote, ALLCOUNTRIES, last_applied=Y - timedelta(days=1))
    remote.publish(Y, [_row(1, "New1", Y), _row(4, "Added4", Y)], deletes=[3])

    apply_daily_updates([ALLCOUNTRIES], {"allcountries"})

    assert _names(engine, AllCountries) == {1: "New1", 2: "Keep2", 4: "Added4"}
    meta = _metadata(remote, ALLCOUNTRIES)
    assert meta["last_applied_date"] == Y
    assert meta["out_of_sync"] is False
    assert meta["needs_rebuild"] is False


def test_city_dataset_only_gets_rows_above_its_population_threshold(engine, remote):
    for ds in (ALLCOUNTRIES, CITIES5000):
        _seed_metadata(remote, ds, last_applied=Y - timedelta(days=1))
    remote.publish(
        Y,
        [_row(1, "Village", Y, population=100), _row(2, "Town", Y, population=10_000)],
        deletes=[],
    )

    apply_daily_updates([ALLCOUNTRIES, CITIES5000], {"allcountries", "cities5000"})

    assert _names(engine, AllCountries) == {1: "Village", 2: "Town"}
    assert _names(engine, Cities5000) == {2: "Town"}


def test_missed_days_are_replayed_in_order(engine, remote):
    _seed_rows(engine, AllCountries, [_geo(1, "Original"), _geo(2, "Keep2")])
    _seed_metadata(remote, ALLCOUNTRIES, last_applied=Y - timedelta(days=3))
    remote.publish(
        Y - timedelta(days=2), [_row(1, "Renamed", Y - timedelta(days=2))], deletes=[]
    )
    remote.publish(
        Y - timedelta(days=1), [_row(5, "Added5", Y - timedelta(days=1))], deletes=[2]
    )
    remote.publish(Y, [_row(1, "RenamedAgain", Y)], deletes=[])

    apply_daily_updates([ALLCOUNTRIES], {"allcountries"})

    assert _names(engine, AllCountries) == {1: "RenamedAgain", 5: "Added5"}
    assert _metadata(remote, ALLCOUNTRIES)["last_applied_date"] == Y


def test_replaying_a_day_is_idempotent(engine, remote):
    _seed_rows(engine, AllCountries, [_geo(1, "Old1"), _geo(2, "Doomed2")])
    remote.publish(Y, [_row(1, "New1", Y), _row(3, "Added3", Y)], deletes=[2])
    _seed_metadata(remote, ALLCOUNTRIES, last_applied=Y - timedelta(days=1))

    apply_daily_updates([ALLCOUNTRIES], {"allcountries"})
    first = _names(engine, AllCountries)
    with remote.session() as s:
        s.get(DatasetMetaData, "allcountries").last_applied_date = Y - timedelta(days=1)
    apply_daily_updates([ALLCOUNTRIES], {"allcountries"})

    assert _names(engine, AllCountries) == first == {1: "New1", 3: "Added3"}


def test_yesterday_without_deletes_file_is_not_applied(engine, remote):
    _seed_rows(engine, AllCountries, [_geo(1, "Old1")])
    _seed_metadata(remote, ALLCOUNTRIES, last_applied=Y - timedelta(days=1))
    remote.publish(Y, [_row(1, "New1", Y)], deletes=None)

    apply_daily_updates([ALLCOUNTRIES], {"allcountries"})

    assert _names(engine, AllCountries) == {1: "Old1"}
    meta = _metadata(remote, ALLCOUNTRIES)
    assert meta["last_applied_date"] == Y - timedelta(days=1)
    assert meta["out_of_sync"] is True


# ---------------------------------------------------------------------------
# needs_rebuild / out_of_sync flags (real ORM event listeners)
# ---------------------------------------------------------------------------


def test_two_missed_days_flag_rebuild_until_caught_up(engine, remote):
    # Remote dump is ~3 days newer than the last local sync.
    _seed_metadata(remote, ALLCOUNTRIES, last_applied=Y - timedelta(days=3))
    before = _metadata(remote, ALLCOUNTRIES)
    assert before["out_of_sync"] is True
    assert before["needs_rebuild"] is True

    for offset in (2, 1, 0):
        day = Y - timedelta(days=offset)
        remote.publish(day, [_row(offset + 1, f"Day{offset}", day)], deletes=[])
    apply_daily_updates([ALLCOUNTRIES], {"allcountries"})

    after = _metadata(remote, ALLCOUNTRIES)
    assert after["last_applied_date"] == Y
    assert after["out_of_sync"] is False
    assert after["needs_rebuild"] is False
    assert _names(engine, AllCountries) == {1: "Day0", 2: "Day1", 3: "Day2"}


def test_gap_over_max_flags_rebuild_and_flag_survives_remote_check(engine, remote):
    _seed_rows(engine, AllCountries, [_geo(1, "Old1")])
    stale = Y - timedelta(days=MAX_CATCHUP_DAYS + 1)
    _seed_metadata(remote, ALLCOUNTRIES, last_applied=stale)
    remote.publish(Y, [_row(1, "New1", Y)], deletes=[])

    apply_daily_updates([ALLCOUNTRIES], {"allcountries"})

    meta = _metadata(remote, ALLCOUNTRIES)
    assert meta["needs_rebuild"] is True
    assert meta["out_of_sync"] is True
    assert meta["last_modified"] is None
    assert meta["last_applied_date"] == stale
    assert _names(engine, AllCountries) == {1: "Old1"}

    # The next HEAD check rewrites the remote fields; the flag must stay set.
    with remote.session() as s:
        record = s.get(DatasetMetaData, "allcountries")
        record.remote_etag = '"newer"'
        record.remote_last_modified = datetime(2026, 3, 19, 3, 0, tzinfo=timezone.utc)
    assert _metadata(remote, ALLCOUNTRIES)["needs_rebuild"] is True


def test_max_gap_is_still_caught_up_without_rebuild(engine, remote):
    _seed_metadata(
        remote, ALLCOUNTRIES, last_applied=Y - timedelta(days=MAX_CATCHUP_DAYS)
    )
    for offset in range(MAX_CATCHUP_DAYS):
        remote.publish(Y - timedelta(days=offset), [], deletes=[])

    apply_daily_updates([ALLCOUNTRIES], {"allcountries"})

    meta = _metadata(remote, ALLCOUNTRIES)
    assert meta["last_applied_date"] == Y
    assert meta["needs_rebuild"] is False


def test_never_built_dataset_flags_rebuild(engine, remote):
    _seed_metadata(remote, CITIES5000, last_applied=None)
    remote.publish(Y, [_row(1, "Town", Y, population=10_000)], deletes=[])

    apply_daily_updates([CITIES5000], {"cities5000"})

    meta = _metadata(remote, CITIES5000)
    assert meta["needs_rebuild"] is True
    assert meta["last_applied_date"] is None
    assert _names(engine, Cities5000) == {}


def test_one_unpublished_day_does_not_flag_rebuild(engine, remote):
    # Synced recently: remote is less than 2 days newer.
    _seed_metadata(
        remote,
        ALLCOUNTRIES,
        last_applied=Y - timedelta(days=1),
        last_modified=datetime(2026, 3, 17, 4, 0, tzinfo=timezone.utc),
    )

    apply_daily_updates([ALLCOUNTRIES], {"allcountries"})

    meta = _metadata(remote, ALLCOUNTRIES)
    assert meta["out_of_sync"] is True
    assert meta["needs_rebuild"] is False
    assert meta["last_applied_date"] == Y - timedelta(days=1)
