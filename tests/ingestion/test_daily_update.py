"""Tests for geonames_server.ingestion.daily_update."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# Fix ENABLED_DATASETS env var format for pydantic (needs JSON list)
_orig_datasets = os.environ.get("ENABLED_DATASETS", "")
if _orig_datasets and not _orig_datasets.startswith("["):
    os.environ["ENABLED_DATASETS"] = f'["{_orig_datasets}"]'

from geonames_server.database.models.dataset_metadata import (
    DatasetMetaData,
    refresh_needs_rebuild,
    refresh_sync_status,
)
from geonames_server.ingestion.daily_update import (
    GEOBASE_COLUMNS,
    MAX_CATCHUP_DAYS,
    POPULATION_THRESHOLDS,
    DailyUpdateError,
    _pending_days,
    _stage_day,
    apply_daily_updates,
    _delete_from_dataset,
    _download_daily_file,
    _ingest_daily_file,
    _ingest_deletions_file,
    _sync_metadata,
    _upsert_modifications,
    _yesterday,
    check_and_apply_daily_updates,
)
from geonames_server.ingestion.pipeline import check_remote_metadata
from geonames_server.ingestion.sync_tracker import DatasetSyncStatus
from geonames_server.utils.utils import current_utc_date
from geonames_server.ingestion.registry import DatasetInfo

if _orig_datasets:
    os.environ["ENABLED_DATASETS"] = _orig_datasets

MODULE = "geonames_server.ingestion.daily_update"
PIPELINE_MODULE = "geonames_server.ingestion.pipeline"


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _make_dataset_info(name: str = "allcountries") -> DatasetInfo:
    """Create a DatasetInfo with a mock model."""
    mock_table = MagicMock()
    mock_table.name = name
    model = MagicMock()
    model.__table__ = mock_table
    return DatasetInfo(
        name=name,
        zip_file=f"{name}.zip",
        txt_file=f"{name}.txt",
        model=model,
    )


def _make_metadata_record(
    dataset: str = "allcountries",
    out_of_sync: bool = True,
    remote_etag: str = '"new"',
    etag: str = '"old"',
) -> MagicMock:
    """Create a mock DatasetMetaData record."""
    record = MagicMock()
    record.dataset = dataset
    record.out_of_sync = out_of_sync
    record.remote_etag = remote_etag
    record.etag = etag
    record.remote_last_modified = datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc)
    record.last_modified = datetime(2026, 3, 17, 12, 0, 0, tzinfo=timezone.utc)
    return record


# ---------------------------------------------------------------------------
# _yesterday
# ---------------------------------------------------------------------------


class TestYesterday:
    def test_returns_date_object(self):
        result = _yesterday()
        assert isinstance(result, date)

    def test_is_one_day_before_today(self):
        assert _yesterday() == current_utc_date() - timedelta(days=1)


# ---------------------------------------------------------------------------
# POPULATION_THRESHOLDS
# ---------------------------------------------------------------------------


class TestPopulationThresholds:
    def test_allcountries_has_zero_threshold(self):
        assert POPULATION_THRESHOLDS["allcountries"] == 0

    def test_cities500_threshold(self):
        assert POPULATION_THRESHOLDS["cities500"] == 500

    def test_cities1000_threshold(self):
        assert POPULATION_THRESHOLDS["cities1000"] == 1000

    def test_cities5000_threshold(self):
        assert POPULATION_THRESHOLDS["cities5000"] == 5000

    def test_cities15000_threshold(self):
        assert POPULATION_THRESHOLDS["cities15000"] == 15000


# ---------------------------------------------------------------------------
# GEOBASE_COLUMNS
# ---------------------------------------------------------------------------


class TestGeobaseColumns:
    def test_columns_is_non_empty_list(self):
        assert isinstance(GEOBASE_COLUMNS, list)
        assert len(GEOBASE_COLUMNS) > 0

    def test_geonameid_is_first_column(self):
        assert GEOBASE_COLUMNS[0] == "geonameid"

    def test_population_column_present(self):
        assert "population" in GEOBASE_COLUMNS

    def test_modification_date_column_present(self):
        assert "modification_date" in GEOBASE_COLUMNS


# ---------------------------------------------------------------------------
# _download_daily_file
# ---------------------------------------------------------------------------


class TestDownloadDailyFile:
    @patch(f"{MODULE}.Downloader")
    @patch(f"{MODULE}.settings")
    def test_returns_none_when_file_does_not_exist(self, mock_settings, mock_dl_cls):
        mock_settings.geonames_url = "https://example.com/"
        client = MagicMock()
        client.exists.return_value = False

        result = _download_daily_file(client, "modifications-2026-03-18.txt")

        assert result is None
        client.exists.assert_called_once_with(
            "https://example.com/modifications-2026-03-18.txt"
        )
        mock_dl_cls.assert_not_called()

    @patch(f"{MODULE}.Downloader")
    @patch(f"{MODULE}.settings")
    def test_returns_path_when_file_exists(self, mock_settings, mock_dl_cls):
        mock_settings.geonames_url = "https://example.com/"
        mock_settings.cache_dir = Path("/tmp/cache")
        mock_settings.data_dir = Path("/tmp/data")

        client = MagicMock()
        client.exists.return_value = True

        mock_dl_result = MagicMock()
        mock_dl_result.path = Path("/tmp/data/modifications-2026-03-18.txt")
        mock_dl_instance = MagicMock()
        mock_dl_instance.download.return_value = mock_dl_result
        mock_dl_instance.__enter__ = MagicMock(return_value=mock_dl_instance)
        mock_dl_instance.__exit__ = MagicMock(return_value=False)
        mock_dl_cls.return_value = mock_dl_instance

        result = _download_daily_file(client, "modifications-2026-03-18.txt")

        assert result == Path("/tmp/data/modifications-2026-03-18.txt")
        mock_dl_cls.assert_called_once_with(
            cache_dir=Path("/tmp/cache"),
            data_dir=Path("/tmp/data"),
            force=True,
        )

    @patch(f"{MODULE}.Downloader")
    @patch(f"{MODULE}.settings")
    def test_download_exception_propagates(self, mock_settings, mock_dl_cls):
        mock_settings.geonames_url = "https://example.com/"
        mock_settings.cache_dir = Path("/tmp/cache")
        mock_settings.data_dir = Path("/tmp/data")

        client = MagicMock()
        client.exists.return_value = True

        mock_dl_instance = MagicMock()
        mock_dl_instance.download.side_effect = RuntimeError("network error")
        mock_dl_instance.__enter__ = MagicMock(return_value=mock_dl_instance)
        mock_dl_instance.__exit__ = MagicMock(return_value=False)
        mock_dl_cls.return_value = mock_dl_instance

        with pytest.raises(RuntimeError):
            _download_daily_file(client, "modifications-2026-03-18.txt")


# ---------------------------------------------------------------------------
# _ingest_daily_file
# ---------------------------------------------------------------------------


class TestIngestDailyFile:
    @patch(f"{MODULE}.psycopg2.connect")
    @patch(f"{MODULE}._get_conn_params", return_value={"host": "localhost"})
    def test_copies_into_temp_table_and_merges(self, mock_conn_params, mock_connect, tmp_path):
        data_file = tmp_path / "modifications-2026-03-18.txt"
        data_file.write_text(
            "123\tName\tAscii\tAlt\t1.0\t2.0\tP\tPPL\tIN\t\tKA\t\t\t\t1000\t\t\tUTC\t2026-03-18\n"
        )

        model = MagicMock()
        model.__table__ = MagicMock()
        model.__table__.name = "modifications"
        model.__table__.columns = [MagicMock(name=n) for n in GEOBASE_COLUMNS]
        for i, c in enumerate(model.__table__.columns):
            c.name = GEOBASE_COLUMNS[i]

        mock_conn, mock_cursor = _mock_psycopg2_conn()
        mock_connect.return_value = mock_conn

        _ingest_daily_file(data_file, model)

        # CREATE TEMP TABLE + MERGE execute calls
        assert mock_cursor.execute.call_count == 2
        # COPY into temp staging table
        mock_cursor.copy_expert.assert_called_once()


# ---------------------------------------------------------------------------
# _ingest_deletions_file  (date-prepending COPY)
# ---------------------------------------------------------------------------


class TestIngestDeletionsFile:
    @patch(f"{MODULE}.psycopg2.connect")
    @patch(f"{MODULE}._get_conn_params", return_value={"host": "localhost"})
    def test_prepends_date_and_copies(self, mock_conn_params, mock_connect, tmp_path):
        """Each line in the file gets deletion_date prepended."""
        deletes_file = tmp_path / "deletes-2026-03-18.txt"
        deletes_file.write_text(
            "8594496\tCoomaditchy Lagoon\tduplicate item\n"
            "779805\tGornitak\tDuplicate of 9536504\n"
        )

        mock_conn, mock_cursor = _mock_psycopg2_conn()
        mock_connect.return_value = mock_conn

        _ingest_deletions_file(deletes_file, date(2026, 3, 18))

        mock_cursor.copy_expert.assert_called_once()
        # The second arg is the StringIO buffer — read from current position
        buf = mock_cursor.copy_expert.call_args[0][1]
        buf.seek(0)
        content = buf.read()
        assert content.startswith("2026-03-18\t")
        lines = content.strip().split("\n")
        assert len(lines) == 2
        assert lines[0] == "2026-03-18\t8594496\tCoomaditchy Lagoon\tduplicate item"
        assert lines[1] == "2026-03-18\t779805\tGornitak\tDuplicate of 9536504"
        # CREATE TEMP TABLE + MERGE execute calls
        assert mock_cursor.execute.call_count == 2

    def test_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            _ingest_deletions_file(Path("/nonexistent/file.txt"), date(2026, 3, 18))

    @patch(f"{MODULE}.psycopg2.connect")
    @patch(f"{MODULE}._get_conn_params", return_value={"host": "localhost"})
    def test_copy_sql_targets_deletions_table(self, mock_conn_params, mock_connect, tmp_path):
        """COPY goes to temp staging table and merge targets deletions."""
        deletes_file = tmp_path / "deletes.txt"
        deletes_file.write_text("123\tSomeName\tsome reason\n")

        mock_conn, mock_cursor = _mock_psycopg2_conn()
        mock_connect.return_value = mock_conn

        _ingest_deletions_file(deletes_file, date(2026, 3, 18))

        copy_sql_str = mock_cursor.copy_expert.call_args[0][0]
        assert "tmp_deletions_daily_stage" in copy_sql_str
        assert "deletion_date" in copy_sql_str
        assert "geonameid" in copy_sql_str

        merge_sql = mock_cursor.execute.call_args_list[1][0][0]
        from psycopg2 import sql as psql

        assert isinstance(merge_sql, psql.Composed)
        rendered = "".join(
            part.string if hasattr(part, "string") else str(part)
            for part in merge_sql._wrapped
        ).lower()
        assert "insert into" in rendered
        assert "on conflict" in rendered


# ---------------------------------------------------------------------------
# _upsert_modifications  (SQL generation / cursor interaction)
# ---------------------------------------------------------------------------


def _mock_psycopg2_conn(rowcount: int = 0):
    """Build a mock psycopg2 connection that works with psycopg2.sql.

    We use a real psycopg2 connection spec via create_autospec so that
    sql.Composed.as_string(conn) can identify it as a connection.
    Alternatively we just patch psycopg2.connect to return a context
    manager that yields a mock with the right cursor interface.
    """
    mock_cursor = MagicMock()
    mock_cursor.rowcount = rowcount
    mock_cursor.execute = MagicMock()

    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    # Make as_string() work: it calls conn.encoding and pgconn internals.
    # Since we can't easily fake that, we intercept execute and check params instead.
    mock_conn.encoding = "UTF8"
    return mock_conn, mock_cursor


class TestUpsertModifications:
    @patch(f"{MODULE}.psycopg2.connect")
    def test_returns_rowcount_without_population_filter(self, mock_connect):
        mock_conn, mock_cursor = _mock_psycopg2_conn(rowcount=42)
        mock_connect.return_value = mock_conn

        result = _upsert_modifications(
            conn_params={"host": "localhost"},
            target_table="allcountries",
            target_date=date(2026, 3, 18),
            population_threshold=0,
        )

        assert result == 42
        mock_cursor.execute.assert_called_once()
        # Only the date param when no population filter
        executed_params = mock_cursor.execute.call_args[0][1]
        assert executed_params == [date(2026, 3, 18)]

    @patch(f"{MODULE}.psycopg2.connect")
    def test_passes_population_param_when_threshold_gt_zero(self, mock_connect):
        mock_conn, mock_cursor = _mock_psycopg2_conn(rowcount=10)
        mock_connect.return_value = mock_conn

        result = _upsert_modifications(
            conn_params={"host": "localhost"},
            target_table="cities5000",
            target_date=date(2026, 3, 18),
            population_threshold=5000,
        )

        assert result == 10
        executed_params = mock_cursor.execute.call_args[0][1]
        assert executed_params == [date(2026, 3, 18), 5000]

    @patch(f"{MODULE}.psycopg2.connect")
    def test_sql_composed_contains_upsert_keywords(self, mock_connect):
        mock_conn, mock_cursor = _mock_psycopg2_conn(rowcount=0)
        mock_connect.return_value = mock_conn

        _upsert_modifications(
            conn_params={"host": "localhost"},
            target_table="allcountries",
            target_date=date(2026, 3, 18),
            population_threshold=0,
        )

        # The first arg is a psycopg2.sql.Composed object; check its string list
        composed = mock_cursor.execute.call_args[0][0]
        from psycopg2 import sql as psql

        assert isinstance(composed, psql.Composed)
        rendered = "".join(
            part.string if hasattr(part, "string") else str(part)
            for part in composed._wrapped
        ).lower()
        assert "on conflict" in rendered
        assert "do update set" in rendered


# ---------------------------------------------------------------------------
# _delete_from_dataset  (SQL generation / cursor interaction)
# ---------------------------------------------------------------------------


class TestDeleteFromDataset:
    @patch(f"{MODULE}.psycopg2.connect")
    def test_returns_rowcount_with_date_filter(self, mock_connect):
        mock_conn, mock_cursor = _mock_psycopg2_conn(rowcount=5)
        mock_connect.return_value = mock_conn

        result = _delete_from_dataset(
            conn_params={"host": "localhost"},
            target_table="cities500",
            target_date=date(2026, 3, 18),
        )

        assert result == 5
        mock_cursor.execute.assert_called_once()
        executed_params = mock_cursor.execute.call_args[0][1]
        assert executed_params == [date(2026, 3, 18)]

    @patch(f"{MODULE}.psycopg2.connect")
    def test_returns_zero_when_no_rows_deleted(self, mock_connect):
        mock_conn, mock_cursor = _mock_psycopg2_conn(rowcount=0)
        mock_connect.return_value = mock_conn

        result = _delete_from_dataset(
            conn_params={"host": "localhost"},
            target_table="allcountries",
            target_date=date(2026, 3, 18),
        )

        assert result == 0


# ---------------------------------------------------------------------------
# _sync_metadata
# ---------------------------------------------------------------------------


class TestSyncMetadata:
    @patch(f"{MODULE}.get_session")
    def test_copies_remote_etag_to_local(self, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        record = _make_metadata_record(
            dataset="allcountries", remote_etag='"new-etag"', etag='"old-etag"'
        )

        with patch(f"{MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_by_dataset.return_value = record
            ds = _make_dataset_info("allcountries")
            _sync_metadata([ds])

        assert record.etag == '"new-etag"'
        mock_session.commit.assert_called()

    @patch(f"{MODULE}.get_session")
    def test_sets_last_modified_to_now(self, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        record = _make_metadata_record(dataset="cities500")

        with patch(f"{MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_by_dataset.return_value = record
            before = datetime.now(timezone.utc)
            _sync_metadata([_make_dataset_info("cities500")])
            after = datetime.now(timezone.utc)

        assert before <= record.last_modified <= after

    @patch(f"{MODULE}.get_session")
    def test_skips_missing_metadata(self, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_by_dataset.return_value = None
            _sync_metadata([_make_dataset_info("nonexistent")])

        # Should not crash; commit only called for datasets that exist
        mock_session.commit.assert_not_called()

    @patch(f"{MODULE}.get_session")
    def test_syncs_multiple_datasets(self, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        records = {
            "allcountries": _make_metadata_record("allcountries", remote_etag='"a"'),
            "cities500": _make_metadata_record("cities500", remote_etag='"b"'),
        }

        with patch(f"{MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_by_dataset.side_effect = lambda n: records.get(n)
            _sync_metadata([
                _make_dataset_info("allcountries"),
                _make_dataset_info("cities500"),
            ])

        assert records["allcountries"].etag == '"a"'
        assert records["cities500"].etag == '"b"'
        assert mock_session.commit.call_count == 2


# ---------------------------------------------------------------------------
# _pending_days
# ---------------------------------------------------------------------------


class TestPendingDays:
    Y = date(2026, 3, 18)

    def test_never_built_needs_rebuild(self):
        assert _pending_days(None, self.Y) is None

    def test_caught_up_is_empty(self):
        assert _pending_days(self.Y, self.Y) == []

    def test_gap_is_replayed_in_order(self):
        assert _pending_days(date(2026, 3, 15), self.Y) == [
            date(2026, 3, 16),
            date(2026, 3, 17),
            date(2026, 3, 18),
        ]

    def test_max_gap_is_replayed(self):
        days = _pending_days(self.Y - timedelta(days=MAX_CATCHUP_DAYS), self.Y)
        assert len(days) == MAX_CATCHUP_DAYS
        assert days[-1] == self.Y

    def test_gap_over_max_needs_rebuild(self):
        assert _pending_days(self.Y - timedelta(days=MAX_CATCHUP_DAYS + 1), self.Y) is None


# ---------------------------------------------------------------------------
# apply_daily_updates / check_and_apply_daily_updates
# ---------------------------------------------------------------------------

Y = date(2026, 3, 18)


@pytest.fixture
def engine():
    """Patch every collaborator of apply_daily_updates; yield the mocks."""
    names = [
        "_yesterday",
        "_load_last_applied",
        "_set_last_applied",
        "_require_rebuild",
        "_stage_day",
        "_upsert_modifications",
        "_delete_from_dataset",
        "_sync_metadata",
        "_get_conn_params",
    ]
    patches = {n: patch(f"{MODULE}.{n}") for n in names}
    mocks = {n: p.start() for n, p in patches.items()}
    patches["enabled"] = patch(
        f"{MODULE}.settings.enabled_datasets",
        ["allcountries", "cities500", "cities5000"],
    )
    patches["enabled"].start()
    mocks["_yesterday"].return_value = Y
    mocks["_stage_day"].return_value = True
    mocks["_upsert_modifications"].return_value = 1
    mocks["_delete_from_dataset"].return_value = 1
    yield mocks
    patch.stopall()


class TestApplyDailyUpdates:
    def test_replays_each_missed_day_in_order(self, engine):
        ds = _make_dataset_info("allcountries")
        engine["_load_last_applied"].return_value = {"allcountries": date(2026, 3, 15)}

        apply_daily_updates([ds], {"allcountries"})

        staged = [c.args[0] for c in engine["_stage_day"].call_args_list]
        assert staged == [date(2026, 3, 16), date(2026, 3, 17), Y]
        partial = [c.kwargs["allow_partial"] for c in engine["_stage_day"].call_args_list]
        assert partial == [True, True, False]
        advanced = [c.args[1] for c in engine["_set_last_applied"].call_args_list]
        assert advanced == staged
        engine["_sync_metadata"].assert_called_once_with([ds])

    def test_failed_dataset_not_synced_and_does_not_skip_days(self, engine):
        ok, bad = _make_dataset_info("cities500"), _make_dataset_info("allcountries")
        engine["_load_last_applied"].return_value = {
            "cities500": date(2026, 3, 16),
            "allcountries": date(2026, 3, 16),
        }

        def upsert(_conn, table, day, _thr):
            if table == "allcountries":
                raise RuntimeError("boom")
            return 1

        engine["_upsert_modifications"].side_effect = upsert
        seen = []

        apply_daily_updates(
            [ok, bad], {"cities500", "allcountries"}, on_dataset=lambda n, s: seen.append((n, s))
        )

        engine["_sync_metadata"].assert_called_once_with([ok])
        assert ("allcountries", DatasetSyncStatus.FAILED) in seen
        # failed dataset is dropped after its first failure, never advanced
        assert all(c.args[0] != "allcountries" for c in engine["_set_last_applied"].call_args_list)
        assert engine["_upsert_modifications"].call_count == 3  # cities500: 2 days; allcountries: 1 (failed, dropped)

    def test_stops_when_yesterdays_files_not_published(self, engine):
        ds = _make_dataset_info()
        engine["_load_last_applied"].return_value = {"allcountries": Y - timedelta(days=1)}
        engine["_stage_day"].return_value = False

        apply_daily_updates([ds], {"allcountries"})

        engine["_set_last_applied"].assert_not_called()
        engine["_upsert_modifications"].assert_not_called()
        engine["_sync_metadata"].assert_called_once_with([])

    def test_old_unpublished_day_is_skipped_but_advanced(self, engine):
        ds = _make_dataset_info()
        engine["_load_last_applied"].return_value = {"allcountries": date(2026, 3, 16)}
        engine["_stage_day"].side_effect = [False, True]  # 03-17 missing, 03-18 ok

        apply_daily_updates([ds], {"allcountries"})

        assert [c.args[1] for c in engine["_set_last_applied"].call_args_list] == [
            date(2026, 3, 17),
            Y,
        ]
        assert engine["_upsert_modifications"].call_count == 1
        engine["_sync_metadata"].assert_called_once_with([ds])

    def test_staging_failure_aborts_without_syncing(self, engine):
        ds = _make_dataset_info()
        engine["_load_last_applied"].return_value = {"allcountries": Y - timedelta(days=1)}
        engine["_stage_day"].side_effect = DailyUpdateError("x")

        with pytest.raises(DailyUpdateError):
            apply_daily_updates([ds], {"allcountries"})

        engine["_sync_metadata"].assert_not_called()
        engine["_set_last_applied"].assert_not_called()

    def test_gap_over_max_marks_rebuild_instead_of_replaying(self, engine):
        stale, ok = _make_dataset_info("allcountries"), _make_dataset_info("cities500")
        engine["_load_last_applied"].return_value = {
            "allcountries": Y - timedelta(days=MAX_CATCHUP_DAYS + 1),
            "cities500": Y - timedelta(days=1),
        }
        seen = []

        apply_daily_updates(
            [stale, ok],
            {"allcountries", "cities500"},
            on_dataset=lambda n, s: seen.append((n, s)),
        )

        engine["_require_rebuild"].assert_called_once_with("allcountries")
        assert ("allcountries", DatasetSyncStatus.FAILED) in seen
        tables = [c.args[1] for c in engine["_upsert_modifications"].call_args_list]
        assert tables == ["cities500"]
        engine["_sync_metadata"].assert_called_once_with([ok])

    def test_never_built_dataset_marks_rebuild(self, engine):
        ds = _make_dataset_info("cities500")
        engine["_load_last_applied"].return_value = {"cities500": None}

        apply_daily_updates([ds], {"cities500"})

        engine["_require_rebuild"].assert_called_once_with("cities500")
        engine["_stage_day"].assert_not_called()
        engine["_sync_metadata"].assert_called_once_with([])

    def test_already_caught_up_dataset_is_just_synced(self, engine):
        ds = _make_dataset_info()
        engine["_load_last_applied"].return_value = {"allcountries": Y}

        apply_daily_updates([ds], {"allcountries"})

        engine["_stage_day"].assert_not_called()
        engine["_sync_metadata"].assert_called_once_with([ds])

    def test_population_threshold_passed_per_dataset(self, engine):
        a, c = _make_dataset_info("allcountries"), _make_dataset_info("cities5000")
        engine["_load_last_applied"].return_value = {
            "allcountries": Y - timedelta(days=1),
            "cities5000": Y - timedelta(days=1),
        }

        apply_daily_updates([a, c], {"allcountries", "cities5000"})

        thresholds = {
            call.args[1]: call.args[3]
            for call in engine["_upsert_modifications"].call_args_list
        }
        assert thresholds == {"allcountries": 0, "cities5000": 5000}

    def test_only_out_of_sync_datasets_are_touched(self, engine):
        a, c = _make_dataset_info("allcountries"), _make_dataset_info("cities500")
        engine["_load_last_applied"].return_value = {"cities500": Y - timedelta(days=1)}

        apply_daily_updates([a, c], {"cities500"})

        engine["_load_last_applied"].assert_called_once_with(["cities500"])
        engine["_sync_metadata"].assert_called_once_with([c])


class TestDisabledDatasets:
    def test_disabled_dataset_is_never_loaded_staged_or_synced(self, engine):
        on, off = _make_dataset_info("cities500"), _make_dataset_info("cities1000")
        engine["_load_last_applied"].return_value = {"cities500": Y - timedelta(days=1)}

        apply_daily_updates([on, off], {"cities500", "cities1000"})

        engine["_load_last_applied"].assert_called_once_with(["cities500"])
        tables = [c.args[1] for c in engine["_upsert_modifications"].call_args_list]
        assert tables == ["cities500"]
        engine["_sync_metadata"].assert_called_once_with([on])

    def test_enabled_names_are_case_and_space_insensitive(self, engine):
        ds = _make_dataset_info("cities500")
        engine["_load_last_applied"].return_value = {"cities500": Y}
        with patch(f"{MODULE}.settings.enabled_datasets", [" Cities500 "]):
            apply_daily_updates([ds], {"cities500"})
        engine["_sync_metadata"].assert_called_once_with([ds])


class TestStageDay:
    @patch(f"{MODULE}._ingest_deletions_file")
    @patch(f"{MODULE}._ingest_daily_file")
    @patch(f"{MODULE}._download_daily_file")
    @patch(f"{MODULE}.RemoteFileClient")
    def test_downloads_and_ingests_both_files(self, _rfc, mock_dl, mock_mod, mock_del):
        mock_dl.side_effect = [Path("/tmp/m.txt"), Path("/tmp/d.txt")]
        assert _stage_day(Y, lambda _s: None) is True
        names = [c.args[1] for c in mock_dl.call_args_list]
        assert names == ["modifications-2026-03-18.txt", "deletes-2026-03-18.txt"]
        mock_mod.assert_called_once()
        mock_del.assert_called_once_with(Path("/tmp/d.txt"), Y)

    @patch(f"{MODULE}._download_daily_file", return_value=None)
    @patch(f"{MODULE}.RemoteFileClient")
    def test_returns_false_when_nothing_published(self, _rfc, _dl):
        assert _stage_day(Y, lambda _s: None) is False

    @patch(f"{MODULE}._ingest_deletions_file")
    @patch(f"{MODULE}._ingest_daily_file")
    @patch(f"{MODULE}._download_daily_file")
    @patch(f"{MODULE}.RemoteFileClient")
    def test_half_published_day_is_not_staged(self, _rfc, mock_dl, mock_mod, mock_del):
        mock_dl.side_effect = [Path("/tmp/m.txt"), None]
        assert _stage_day(Y, lambda _s: None) is False
        mock_mod.assert_not_called()
        mock_del.assert_not_called()

    @patch(f"{MODULE}._ingest_deletions_file")
    @patch(f"{MODULE}._ingest_daily_file")
    @patch(f"{MODULE}._download_daily_file")
    @patch(f"{MODULE}.RemoteFileClient")
    def test_half_published_day_is_staged_when_partial_allowed(
        self, _rfc, mock_dl, mock_mod, mock_del
    ):
        mock_dl.side_effect = [Path("/tmp/m.txt"), None]
        assert _stage_day(Y, lambda _s: None, allow_partial=True) is True
        mock_mod.assert_called_once()
        mock_del.assert_not_called()

    @patch(f"{MODULE}._download_daily_file", side_effect=RuntimeError("net"))
    @patch(f"{MODULE}.RemoteFileClient")
    def test_download_failure_raises_daily_update_error(self, _rfc, _dl):
        with pytest.raises(DailyUpdateError):
            _stage_day(Y, lambda _s: None)

    @patch(f"{MODULE}._ingest_daily_file", side_effect=RuntimeError("db"))
    @patch(f"{MODULE}._download_daily_file", return_value=Path("/tmp/m.txt"))
    @patch(f"{MODULE}.RemoteFileClient")
    def test_ingest_failure_raises_daily_update_error(self, _rfc, _dl, _ing):
        with pytest.raises(DailyUpdateError):
            _stage_day(Y, lambda _s: None)


class TestCheckAndApplyDailyUpdates:
    @patch(f"{MODULE}.apply_daily_updates")
    @patch(f"{MODULE}.get_session")
    @patch(f"{MODULE}.check_remote_metadata")
    @patch(f"{MODULE}.get_all_datasets")
    def _run(self, out_of_sync, mock_all, mock_check, mock_session, mock_apply, **kw):
        ds = [_make_dataset_info("allcountries"), _make_dataset_info("cities500")]
        mock_all.return_value = ds
        mock_session.return_value.__enter__ = MagicMock()
        mock_session.return_value.__exit__ = MagicMock(return_value=False)
        if "side_effect" in kw:
            mock_apply.side_effect = kw["side_effect"]
        with patch(f"{MODULE}.DatasetMetaDataRepository") as repo:
            repo.return_value.get_out_of_sync.return_value = [
                _make_metadata_record(n) for n in out_of_sync
            ]
            check_and_apply_daily_updates()
        mock_check.assert_called_once_with(ds)
        return mock_apply, ds

    def test_exits_early_when_all_in_sync(self):
        mock_apply, _ = self._run([])
        mock_apply.assert_not_called()

    def test_applies_only_out_of_sync_names(self):
        mock_apply, ds = self._run(["cities500"])
        mock_apply.assert_called_once_with(ds, {"cities500"})

    def test_ignores_out_of_sync_datasets_that_are_disabled(self):
        with patch(f"{MODULE}.settings.enabled_datasets", ["cities500"]):
            mock_apply, ds = self._run(["cities500", "allcountries"])
        mock_apply.assert_called_once_with(ds, {"cities500"})

    def test_nothing_to_do_when_only_disabled_are_out_of_sync(self):
        with patch(f"{MODULE}.settings.enabled_datasets", ["cities500"]):
            mock_apply, _ = self._run(["allcountries"])
        mock_apply.assert_not_called()

    def test_swallows_daily_update_error(self):
        self._run(["cities500"], side_effect=DailyUpdateError("x"))


# ---------------------------------------------------------------------------
# refresh_sync_status  (etag / last-modified comparison logic)
# ---------------------------------------------------------------------------


class TestRefreshSyncStatus:
    """Directly test the event listener logic that computes out_of_sync."""

    def _make_target(
        self,
        etag="abc",
        remote_etag="abc",
        last_modified=None,
        remote_last_modified=None,
    ):
        """Create a plain object with the fields refresh_sync_status reads."""
        now = datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc)
        target = MagicMock(spec=DatasetMetaData)
        target.dataset = "test"
        target.etag = etag
        target.remote_etag = remote_etag
        target.last_modified = last_modified or now
        target.remote_last_modified = remote_last_modified or now
        return target

    def test_in_sync_when_etag_matches_and_remote_not_newer(self):
        target = self._make_target(
            etag='"abc"',
            remote_etag='"abc"',
            last_modified=datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc),
            remote_last_modified=datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc),
        )
        refresh_sync_status(target)
        assert target.out_of_sync is False

    def test_out_of_sync_when_etag_differs(self):
        target = self._make_target(
            etag='"old-etag"',
            remote_etag='"new-etag"',
            last_modified=datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc),
            remote_last_modified=datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc),
        )
        refresh_sync_status(target)
        assert target.out_of_sync is True

    def test_out_of_sync_when_remote_last_modified_is_newer(self):
        target = self._make_target(
            etag='"same"',
            remote_etag='"same"',
            last_modified=datetime(2026, 3, 17, 12, 0, 0, tzinfo=timezone.utc),
            remote_last_modified=datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc),
        )
        refresh_sync_status(target)
        assert target.out_of_sync is True

    def test_out_of_sync_when_both_etag_and_last_modified_differ(self):
        target = self._make_target(
            etag='"old"',
            remote_etag='"new"',
            last_modified=datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc),
            remote_last_modified=datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc),
        )
        refresh_sync_status(target)
        assert target.out_of_sync is True

    def test_in_sync_when_remote_last_modified_is_older_and_etag_matches(self):
        target = self._make_target(
            etag='"same"',
            remote_etag='"same"',
            last_modified=datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc),
            remote_last_modified=datetime(2026, 3, 17, 12, 0, 0, tzinfo=timezone.utc),
        )
        refresh_sync_status(target)
        assert target.out_of_sync is False

    def test_out_of_sync_when_etag_is_none(self):
        target = self._make_target(
            etag=None,
            remote_etag='"new"',
        )
        refresh_sync_status(target)
        assert target.out_of_sync is True

    def test_out_of_sync_when_remote_etag_is_none(self):
        target = self._make_target(
            etag='"old"',
            remote_etag=None,
        )
        refresh_sync_status(target)
        assert target.out_of_sync is True

    def test_out_of_sync_when_last_modified_is_none(self):
        target = self._make_target(
            etag='"same"',
            remote_etag='"same"',
            last_modified=None,
            remote_last_modified=datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc),
        )
        # last_modified = None, so we pass MagicMock's default which is truthy
        # Need to explicitly set it
        target.last_modified = None
        refresh_sync_status(target)
        assert target.out_of_sync is True

    def test_out_of_sync_when_remote_last_modified_is_none(self):
        target = self._make_target(
            etag='"same"',
            remote_etag='"same"',
        )
        target.remote_last_modified = None
        refresh_sync_status(target)
        assert target.out_of_sync is True

    def test_out_of_sync_when_all_fields_none(self):
        target = self._make_target()
        target.etag = None
        target.remote_etag = None
        target.last_modified = None
        target.remote_last_modified = None
        refresh_sync_status(target)
        assert target.out_of_sync is True

    def test_microseconds_are_ignored_in_comparison(self):
        """Ensure sub-second differences don't cause false out_of_sync."""
        target = self._make_target(
            etag='"same"',
            remote_etag='"same"',
            last_modified=datetime(2026, 3, 18, 12, 0, 0, 123456, tzinfo=timezone.utc),
            remote_last_modified=datetime(2026, 3, 18, 12, 0, 0, 654321, tzinfo=timezone.utc),
        )
        refresh_sync_status(target)
        assert target.out_of_sync is False


# ---------------------------------------------------------------------------
# TestRefreshNeedsRebuild
# ---------------------------------------------------------------------------


class TestRefreshNeedsRebuild:
    """Test the needs_rebuild logic (>= 2 days difference triggers rebuild)."""

    def _make_target(self, last_modified=None, remote_last_modified=None):
        target = MagicMock(spec=DatasetMetaData)
        target.dataset = "test"
        target.last_modified = last_modified
        target.remote_last_modified = remote_last_modified
        return target

    def test_needs_rebuild_when_last_modified_is_none(self):
        target = self._make_target(
            last_modified=None,
            remote_last_modified=datetime(2026, 3, 18, tzinfo=timezone.utc),
        )
        refresh_needs_rebuild(target)
        assert target.needs_rebuild is True

    def test_needs_rebuild_when_remote_last_modified_is_none(self):
        target = self._make_target(
            last_modified=datetime(2026, 3, 18, tzinfo=timezone.utc),
            remote_last_modified=None,
        )
        refresh_needs_rebuild(target)
        assert target.needs_rebuild is True

    def test_needs_rebuild_when_difference_is_two_days(self):
        target = self._make_target(
            last_modified=datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc),
            remote_last_modified=datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc),
        )
        refresh_needs_rebuild(target)
        assert target.needs_rebuild is True

    def test_no_rebuild_when_difference_is_one_day(self):
        target = self._make_target(
            last_modified=datetime(2026, 3, 17, 12, 0, 0, tzinfo=timezone.utc),
            remote_last_modified=datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc),
        )
        refresh_needs_rebuild(target)
        assert target.needs_rebuild is False

    def test_no_rebuild_when_same_timestamp(self):
        target = self._make_target(
            last_modified=datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc),
            remote_last_modified=datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc),
        )
        refresh_needs_rebuild(target)
        assert target.needs_rebuild is False


# ---------------------------------------------------------------------------
# check_remote_metadata  (HEAD-check, etag/last-modified update on records)
# ---------------------------------------------------------------------------


class TestCheckRemoteMetadata:
    """Test that check_remote_metadata issues HEAD requests and updates the
    remote_etag / remote_last_modified fields on dataset metadata records."""

    @patch(f"{PIPELINE_MODULE}.settings")
    @patch(f"{PIPELINE_MODULE}.get_session")
    @patch(f"{PIPELINE_MODULE}.RemoteFileClient")
    def test_updates_remote_etag_and_last_modified_from_head(
        self, mock_rfc_cls, mock_get_session, mock_settings
    ):
        mock_settings.geonames_url = "https://download.geonames.org/export/dump/"

        mock_client = MagicMock()
        mock_rfc_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_rfc_cls.return_value.__exit__ = MagicMock(return_value=False)

        remote_meta = MagicMock()
        remote_meta.etag = '"new-etag-123"'
        remote_meta.last_modified = datetime(2026, 3, 18, 15, 0, 0, tzinfo=timezone.utc)
        mock_client.head.return_value = remote_meta

        record = MagicMock()
        record.dataset = "allcountries"
        record.etag = '"old-etag"'
        record.remote_etag = '"old-etag"'
        record.last_modified = datetime(2026, 3, 17, 12, 0, 0, tzinfo=timezone.utc)
        record.remote_last_modified = datetime(2026, 3, 17, 12, 0, 0, tzinfo=timezone.utc)
        record.out_of_sync = False
        record.needs_rebuild = False

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{PIPELINE_MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_by_dataset.return_value = record

            ds = _make_dataset_info("allcountries")
            check_remote_metadata([ds])

        # Verify remote fields were updated from HEAD response
        assert record.remote_etag == '"new-etag-123"'
        assert record.remote_last_modified == datetime(
            2026, 3, 18, 15, 0, 0, tzinfo=timezone.utc
        )
        mock_session.commit.assert_called_once()

    @patch(f"{PIPELINE_MODULE}.settings")
    @patch(f"{PIPELINE_MODULE}.get_session")
    @patch(f"{PIPELINE_MODULE}.RemoteFileClient")
    def test_uses_zip_file_url_when_available(
        self, mock_rfc_cls, mock_get_session, mock_settings
    ):
        mock_settings.geonames_url = "https://download.geonames.org/export/dump/"

        mock_client = MagicMock()
        mock_rfc_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_rfc_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_client.head.return_value = MagicMock(etag='"e"', last_modified=None)

        record = MagicMock()
        record.dataset = "allcountries"
        record.etag = None
        record.remote_etag = None
        record.last_modified = None
        record.remote_last_modified = None

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{PIPELINE_MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_by_dataset.return_value = record

            ds = DatasetInfo(
                name="allcountries",
                zip_file="allCountries.zip",
                txt_file="allCountries.txt",
                model=MagicMock(),
            )
            check_remote_metadata([ds])

        mock_client.head.assert_called_once_with(
            "https://download.geonames.org/export/dump/allCountries.zip"
        )

    @patch(f"{PIPELINE_MODULE}.settings")
    @patch(f"{PIPELINE_MODULE}.get_session")
    @patch(f"{PIPELINE_MODULE}.RemoteFileClient")
    def test_uses_txt_file_url_when_zip_is_empty(
        self, mock_rfc_cls, mock_get_session, mock_settings
    ):
        mock_settings.geonames_url = "https://download.geonames.org/export/dump/"

        mock_client = MagicMock()
        mock_rfc_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_rfc_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_client.head.return_value = MagicMock(etag='"e"', last_modified=None)

        record = MagicMock()
        record.dataset = "testdata"
        record.etag = None
        record.remote_etag = None
        record.last_modified = None
        record.remote_last_modified = None

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{PIPELINE_MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_by_dataset.return_value = record

            ds = DatasetInfo(
                name="testdata",
                zip_file="",
                txt_file="testdata.txt",
                model=MagicMock(),
            )
            check_remote_metadata([ds])

        mock_client.head.assert_called_once_with(
            "https://download.geonames.org/export/dump/testdata.txt"
        )

    @patch(f"{PIPELINE_MODULE}.settings")
    @patch(f"{PIPELINE_MODULE}.get_session")
    @patch(f"{PIPELINE_MODULE}.RemoteFileClient")
    def test_skips_dataset_when_head_request_fails(
        self, mock_rfc_cls, mock_get_session, mock_settings
    ):
        mock_settings.geonames_url = "https://example.com/"

        mock_client = MagicMock()
        mock_rfc_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_rfc_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_client.head.side_effect = ConnectionError("network down")

        record = MagicMock()
        record.dataset = "allcountries"
        record.remote_etag = '"old"'

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{PIPELINE_MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_by_dataset.return_value = record

            ds = _make_dataset_info("allcountries")
            check_remote_metadata([ds])

        # remote_etag not updated
        assert record.remote_etag == '"old"'
        mock_session.commit.assert_not_called()

    @patch(f"{PIPELINE_MODULE}.settings")
    @patch(f"{PIPELINE_MODULE}.get_session")
    @patch(f"{PIPELINE_MODULE}.RemoteFileClient")
    def test_skips_dataset_when_no_metadata_record(
        self, mock_rfc_cls, mock_get_session, mock_settings
    ):
        mock_settings.geonames_url = "https://example.com/"

        mock_client = MagicMock()
        mock_rfc_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_rfc_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_client.head.return_value = MagicMock(etag='"e"', last_modified=None)

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{PIPELINE_MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_by_dataset.return_value = None

            ds = _make_dataset_info("allcountries")
            # Should not raise — just skip
            check_remote_metadata([ds])

        # commit not called since no record to update
        mock_session.commit.assert_not_called()

    @patch(f"{PIPELINE_MODULE}.settings")
    @patch(f"{PIPELINE_MODULE}.get_session")
    @patch(f"{PIPELINE_MODULE}.RemoteFileClient")
    def test_processes_multiple_datasets(
        self, mock_rfc_cls, mock_get_session, mock_settings
    ):
        mock_settings.geonames_url = "https://example.com/"

        mock_client = MagicMock()
        mock_rfc_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_rfc_cls.return_value.__exit__ = MagicMock(return_value=False)

        meta1 = MagicMock(etag='"etag-all"', last_modified=datetime(2026, 3, 18, tzinfo=timezone.utc))
        meta2 = MagicMock(etag='"etag-500"', last_modified=datetime(2026, 3, 17, tzinfo=timezone.utc))
        mock_client.head.side_effect = [meta1, meta2]

        rec1 = MagicMock(dataset="allcountries", etag=None, remote_etag=None)
        rec2 = MagicMock(dataset="cities500", etag=None, remote_etag=None)

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{PIPELINE_MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_by_dataset.side_effect = [rec1, rec2]

            ds1 = _make_dataset_info("allcountries")
            ds2 = _make_dataset_info("cities500")
            check_remote_metadata([ds1, ds2])

        assert rec1.remote_etag == '"etag-all"'
        assert rec2.remote_etag == '"etag-500"'
        assert rec1.remote_last_modified == datetime(2026, 3, 18, tzinfo=timezone.utc)
        assert rec2.remote_last_modified == datetime(2026, 3, 17, tzinfo=timezone.utc)
        assert mock_session.commit.call_count == 2
