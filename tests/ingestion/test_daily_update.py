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
    POPULATION_THRESHOLDS,
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
        assert _yesterday() == date.today() - timedelta(days=1)


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
    def test_returns_none_on_download_exception(self, mock_settings, mock_dl_cls):
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

        result = _download_daily_file(client, "modifications-2026-03-18.txt")
        assert result is None


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
# check_and_apply_daily_updates  (orchestration)
# ---------------------------------------------------------------------------


class TestCheckAndApplyDailyUpdates:
    @patch(f"{MODULE}._sync_metadata")
    @patch(f"{MODULE}._delete_from_dataset")
    @patch(f"{MODULE}._upsert_modifications")
    @patch(f"{MODULE}._ingest_deletions_file")
    @patch(f"{MODULE}._ingest_daily_file")
    @patch(f"{MODULE}._download_daily_file")
    @patch(f"{MODULE}.RemoteFileClient")
    @patch(f"{MODULE}.get_session")
    @patch(f"{MODULE}.check_remote_metadata")
    @patch(f"{MODULE}.get_all_datasets")
    @patch(f"{MODULE}._get_conn_params", return_value={"host": "localhost"})
    def test_full_pipeline_with_out_of_sync_datasets(
        self,
        mock_conn,
        mock_get_all,
        mock_check_remote,
        mock_get_session,
        mock_rfc,
        mock_download,
        mock_ingest,
        mock_ingest_del,
        mock_upsert,
        mock_delete,
        mock_sync,
    ):
        """Happy path: datasets are out of sync, daily files exist."""
        ds_all = _make_dataset_info("allcountries")
        ds_500 = _make_dataset_info("cities500")
        mock_get_all.return_value = [ds_all, ds_500]

        # Simulate out-of-sync records
        oos_record1 = MagicMock()
        oos_record1.dataset = "allcountries"
        oos_record2 = MagicMock()
        oos_record2.dataset = "cities500"

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_out_of_sync.return_value = [
                oos_record1,
                oos_record2,
            ]

            # Daily files are found
            mock_client = MagicMock()
            mock_rfc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_rfc.return_value.__exit__ = MagicMock(return_value=False)
            mock_download.side_effect = [
                Path("/tmp/modifications.txt"),
                Path("/tmp/deletes.txt"),
            ]

            mock_upsert.return_value = 10
            mock_delete.return_value = 2

            check_and_apply_daily_updates()

        # Step 1a
        mock_check_remote.assert_called_once()
        # Step 1b – download called twice (mod + del)
        assert mock_download.call_count == 2
        # Modifications ingested via _ingest_daily_file
        assert mock_ingest.call_count == 1
        # Deletions ingested via _ingest_deletions_file
        assert mock_ingest_del.call_count == 1
        # Step 2 – upsert + delete for each dataset (2 × 2 = 4 total)
        assert mock_upsert.call_count == 2
        assert mock_delete.call_count == 2
        # Step 3
        mock_sync.assert_called_once()

    @patch(f"{MODULE}.get_session")
    @patch(f"{MODULE}.check_remote_metadata")
    @patch(f"{MODULE}.get_all_datasets")
    def test_exits_early_when_all_in_sync(
        self, mock_get_all, mock_check_remote, mock_get_session
    ):
        """When no dataset is out of sync, nothing else happens."""
        mock_get_all.return_value = [_make_dataset_info("allcountries")]

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_out_of_sync.return_value = []
            check_and_apply_daily_updates()

        mock_check_remote.assert_called_once()
        # No download or ingestion should happen

    @patch(f"{MODULE}._download_daily_file")
    @patch(f"{MODULE}.RemoteFileClient")
    @patch(f"{MODULE}.get_session")
    @patch(f"{MODULE}.check_remote_metadata")
    @patch(f"{MODULE}.get_all_datasets")
    def test_exits_early_when_no_daily_files(
        self,
        mock_get_all,
        mock_check_remote,
        mock_get_session,
        mock_rfc,
        mock_download,
    ):
        """When daily files don't exist (both None), exits without ingestion."""
        mock_get_all.return_value = [_make_dataset_info("allcountries")]

        oos = MagicMock()
        oos.dataset = "allcountries"
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_out_of_sync.return_value = [oos]

            mock_client = MagicMock()
            mock_rfc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_rfc.return_value.__exit__ = MagicMock(return_value=False)
            mock_download.return_value = None

            check_and_apply_daily_updates()

        # download attempted for both mod and del files
        assert mock_download.call_count == 2

    @patch(f"{MODULE}._sync_metadata")
    @patch(f"{MODULE}._delete_from_dataset")
    @patch(f"{MODULE}._upsert_modifications")
    @patch(f"{MODULE}._ingest_deletions_file")
    @patch(f"{MODULE}._ingest_daily_file")
    @patch(f"{MODULE}._download_daily_file")
    @patch(f"{MODULE}.RemoteFileClient")
    @patch(f"{MODULE}.get_session")
    @patch(f"{MODULE}.check_remote_metadata")
    @patch(f"{MODULE}.get_all_datasets")
    @patch(f"{MODULE}._get_conn_params", return_value={"host": "localhost"})
    def test_only_processes_out_of_sync_datasets(
        self,
        mock_conn,
        mock_get_all,
        mock_check_remote,
        mock_get_session,
        mock_rfc,
        mock_download,
        mock_ingest,
        mock_ingest_del,
        mock_upsert,
        mock_delete,
        mock_sync,
    ):
        """Only out-of-sync datasets get upserts/deletes applied."""
        ds_all = _make_dataset_info("allcountries")
        ds_500 = _make_dataset_info("cities500")
        ds_1000 = _make_dataset_info("cities1000")
        mock_get_all.return_value = [ds_all, ds_500, ds_1000]

        # Only allcountries is out of sync
        oos = MagicMock()
        oos.dataset = "allcountries"

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_out_of_sync.return_value = [oos]

            mock_client = MagicMock()
            mock_rfc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_rfc.return_value.__exit__ = MagicMock(return_value=False)
            mock_download.side_effect = [
                Path("/tmp/modifications.txt"),
                Path("/tmp/deletes.txt"),
            ]
            mock_upsert.return_value = 1
            mock_delete.return_value = 1

            check_and_apply_daily_updates()

        # Only 1 dataset (allcountries) should get upsert + delete
        assert mock_upsert.call_count == 1
        assert mock_delete.call_count == 1
        # The upsert should use allcountries threshold (0)
        upsert_call = mock_upsert.call_args
        assert upsert_call[1].get("population_threshold", upsert_call[0][3]) == 0

    @patch(f"{MODULE}._sync_metadata")
    @patch(f"{MODULE}._delete_from_dataset")
    @patch(f"{MODULE}._upsert_modifications")
    @patch(f"{MODULE}._ingest_daily_file")
    @patch(f"{MODULE}._download_daily_file")
    @patch(f"{MODULE}.RemoteFileClient")
    @patch(f"{MODULE}.get_session")
    @patch(f"{MODULE}.check_remote_metadata")
    @patch(f"{MODULE}.get_all_datasets")
    @patch(f"{MODULE}._get_conn_params", return_value={"host": "localhost"})
    def test_only_modifications_when_deletes_unavailable(
        self,
        mock_conn,
        mock_get_all,
        mock_check_remote,
        mock_get_session,
        mock_rfc,
        mock_download,
        mock_ingest,
        mock_upsert,
        mock_delete,
        mock_sync,
    ):
        """When only modifications file exists, deletions are skipped."""
        ds = _make_dataset_info("allcountries")
        mock_get_all.return_value = [ds]

        oos = MagicMock()
        oos.dataset = "allcountries"

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_out_of_sync.return_value = [oos]

            mock_client = MagicMock()
            mock_rfc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_rfc.return_value.__exit__ = MagicMock(return_value=False)
            # mod exists, del doesn't
            mock_download.side_effect = [Path("/tmp/mod.txt"), None]
            mock_upsert.return_value = 5

            check_and_apply_daily_updates()

        # Ingestion only for mod file
        assert mock_ingest.call_count == 1
        # Upsert called, delete not called
        assert mock_upsert.call_count == 1
        mock_delete.assert_not_called()
        # Sync still runs
        mock_sync.assert_called_once()

    @patch(f"{MODULE}._sync_metadata")
    @patch(f"{MODULE}._delete_from_dataset")
    @patch(f"{MODULE}._upsert_modifications")
    @patch(f"{MODULE}._ingest_deletions_file")
    @patch(f"{MODULE}._ingest_daily_file")
    @patch(f"{MODULE}._download_daily_file")
    @patch(f"{MODULE}.RemoteFileClient")
    @patch(f"{MODULE}.get_session")
    @patch(f"{MODULE}.check_remote_metadata")
    @patch(f"{MODULE}.get_all_datasets")
    @patch(f"{MODULE}._get_conn_params", return_value={"host": "localhost"})
    def test_only_deletions_when_modifications_unavailable(
        self,
        mock_conn,
        mock_get_all,
        mock_check_remote,
        mock_get_session,
        mock_rfc,
        mock_download,
        mock_ingest,
        mock_ingest_del,
        mock_upsert,
        mock_delete,
        mock_sync,
    ):
        """When only deletes file exists, modifications are skipped."""
        ds = _make_dataset_info("allcountries")
        mock_get_all.return_value = [ds]

        oos = MagicMock()
        oos.dataset = "allcountries"

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_out_of_sync.return_value = [oos]

            mock_client = MagicMock()
            mock_rfc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_rfc.return_value.__exit__ = MagicMock(return_value=False)
            # mod doesn't exist, del does
            mock_download.side_effect = [None, Path("/tmp/del.txt")]
            mock_delete.return_value = 3

            check_and_apply_daily_updates()

        # No modifications ingested
        mock_ingest.assert_not_called()
        # Deletions ingested via _ingest_deletions_file
        assert mock_ingest_del.call_count == 1
        # Delete called, upsert not called
        mock_upsert.assert_not_called()
        assert mock_delete.call_count == 1
        mock_sync.assert_called_once()

    @patch(f"{MODULE}._sync_metadata")
    @patch(f"{MODULE}._delete_from_dataset")
    @patch(f"{MODULE}._upsert_modifications")
    @patch(f"{MODULE}._ingest_deletions_file")
    @patch(f"{MODULE}._ingest_daily_file")
    @patch(f"{MODULE}._download_daily_file")
    @patch(f"{MODULE}.RemoteFileClient")
    @patch(f"{MODULE}.get_session")
    @patch(f"{MODULE}.check_remote_metadata")
    @patch(f"{MODULE}.get_all_datasets")
    @patch(f"{MODULE}._get_conn_params", return_value={"host": "localhost"})
    def test_returns_early_if_mod_ingestion_fails(
        self,
        mock_conn,
        mock_get_all,
        mock_check_remote,
        mock_get_session,
        mock_rfc,
        mock_download,
        mock_ingest,
        mock_ingest_del,
        mock_upsert,
        mock_delete,
        mock_sync,
    ):
        """If modifications ingestion fails, pipeline aborts."""
        ds = _make_dataset_info("allcountries")
        mock_get_all.return_value = [ds]

        oos = MagicMock()
        oos.dataset = "allcountries"

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_out_of_sync.return_value = [oos]

            mock_client = MagicMock()
            mock_rfc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_rfc.return_value.__exit__ = MagicMock(return_value=False)
            mock_download.side_effect = [Path("/tmp/mod.txt"), Path("/tmp/del.txt")]
            mock_ingest.side_effect = RuntimeError("COPY failed")

            check_and_apply_daily_updates()

        # Should not proceed to upsert/delete/sync
        mock_upsert.assert_not_called()
        mock_delete.assert_not_called()
        mock_sync.assert_not_called()

    @patch(f"{MODULE}._sync_metadata")
    @patch(f"{MODULE}._delete_from_dataset")
    @patch(f"{MODULE}._upsert_modifications")
    @patch(f"{MODULE}._ingest_daily_file")
    @patch(f"{MODULE}._download_daily_file")
    @patch(f"{MODULE}.RemoteFileClient")
    @patch(f"{MODULE}.get_session")
    @patch(f"{MODULE}.check_remote_metadata")
    @patch(f"{MODULE}.get_all_datasets")
    @patch(f"{MODULE}._get_conn_params", return_value={"host": "localhost"})
    def test_population_thresholds_passed_per_dataset(
        self,
        mock_conn,
        mock_get_all,
        mock_check_remote,
        mock_get_session,
        mock_rfc,
        mock_download,
        mock_ingest,
        mock_upsert,
        mock_delete,
        mock_sync,
    ):
        """Each dataset gets the correct population threshold."""
        datasets = [
            _make_dataset_info("allcountries"),
            _make_dataset_info("cities500"),
            _make_dataset_info("cities5000"),
            _make_dataset_info("cities15000"),
        ]
        mock_get_all.return_value = datasets

        oos_records = [MagicMock(dataset=ds.name) for ds in datasets]

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_out_of_sync.return_value = oos_records

            mock_client = MagicMock()
            mock_rfc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_rfc.return_value.__exit__ = MagicMock(return_value=False)
            mock_download.side_effect = [Path("/tmp/mod.txt"), None]
            mock_upsert.return_value = 1

            check_and_apply_daily_updates()

        # Verify each call used the right threshold
        assert mock_upsert.call_count == 4
        thresholds_used = [c[0][3] for c in mock_upsert.call_args_list]
        assert thresholds_used == [0, 500, 5000, 15000]

    @patch(f"{MODULE}._sync_metadata")
    @patch(f"{MODULE}._delete_from_dataset")
    @patch(f"{MODULE}._upsert_modifications")
    @patch(f"{MODULE}._ingest_deletions_file")
    @patch(f"{MODULE}._ingest_daily_file")
    @patch(f"{MODULE}._download_daily_file")
    @patch(f"{MODULE}.RemoteFileClient")
    @patch(f"{MODULE}.get_session")
    @patch(f"{MODULE}.check_remote_metadata")
    @patch(f"{MODULE}.get_all_datasets")
    @patch(f"{MODULE}._get_conn_params", return_value={"host": "localhost"})
    def test_upsert_exception_does_not_block_other_datasets(
        self,
        mock_conn,
        mock_get_all,
        mock_check_remote,
        mock_get_session,
        mock_rfc,
        mock_download,
        mock_ingest,
        mock_ingest_del,
        mock_upsert,
        mock_delete,
        mock_sync,
    ):
        """If upsert fails for one dataset, others still proceed."""
        ds1 = _make_dataset_info("allcountries")
        ds2 = _make_dataset_info("cities500")
        mock_get_all.return_value = [ds1, ds2]

        oos = [MagicMock(dataset="allcountries"), MagicMock(dataset="cities500")]
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_out_of_sync.return_value = oos

            mock_client = MagicMock()
            mock_rfc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_rfc.return_value.__exit__ = MagicMock(return_value=False)
            mock_download.side_effect = [Path("/tmp/mod.txt"), Path("/tmp/del.txt")]

            # First upsert fails, second succeeds
            mock_upsert.side_effect = [RuntimeError("DB error"), 5]
            mock_delete.return_value = 1

            check_and_apply_daily_updates()

        # Both datasets attempted upsert
        assert mock_upsert.call_count == 2
        # deletions still applied to both
        assert mock_delete.call_count == 2
        # sync still runs
        mock_sync.assert_called_once()


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


# ---------------------------------------------------------------------------
# End-to-end etag/last-modified triggering in check_and_apply_daily_updates
# ---------------------------------------------------------------------------


class TestEtagLastModifiedTriggering:
    """Verify that check_and_apply_daily_updates proceeds or skips based on
    the out_of_sync flag that is driven by etag/last-modified comparisons."""

    @patch(f"{MODULE}._sync_metadata")
    @patch(f"{MODULE}._delete_from_dataset", return_value=0)
    @patch(f"{MODULE}._upsert_modifications", return_value=0)
    @patch(f"{MODULE}._ingest_daily_file")
    @patch(f"{MODULE}._download_daily_file")
    @patch(f"{MODULE}.RemoteFileClient")
    @patch(f"{MODULE}.get_session")
    @patch(f"{MODULE}.check_remote_metadata")
    @patch(f"{MODULE}.get_all_datasets")
    @patch(f"{MODULE}._get_conn_params", return_value={"host": "localhost"})
    def test_matching_etag_means_in_sync_skips_update(
        self,
        mock_conn,
        mock_get_all,
        mock_check_remote,
        mock_get_session,
        mock_rfc,
        mock_download,
        mock_ingest,
        mock_upsert,
        mock_delete,
        mock_sync,
    ):
        """When etag matches and remote is not newer, out_of_sync=False →
        no datasets to update → early return."""
        mock_get_all.return_value = [_make_dataset_info("allcountries")]

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{MODULE}.DatasetMetaDataRepository") as MockRepo:
            # No out-of-sync records → etag and last-modified matched
            MockRepo.return_value.get_out_of_sync.return_value = []

            check_and_apply_daily_updates()

        mock_check_remote.assert_called_once()
        mock_download.assert_not_called()
        mock_ingest.assert_not_called()
        mock_upsert.assert_not_called()
        mock_delete.assert_not_called()
        mock_sync.assert_not_called()

    @patch(f"{MODULE}._sync_metadata")
    @patch(f"{MODULE}._delete_from_dataset", return_value=1)
    @patch(f"{MODULE}._upsert_modifications", return_value=5)
    @patch(f"{MODULE}._ingest_deletions_file")
    @patch(f"{MODULE}._ingest_daily_file")
    @patch(f"{MODULE}._download_daily_file")
    @patch(f"{MODULE}.RemoteFileClient")
    @patch(f"{MODULE}.get_session")
    @patch(f"{MODULE}.check_remote_metadata")
    @patch(f"{MODULE}.get_all_datasets")
    @patch(f"{MODULE}._get_conn_params", return_value={"host": "localhost"})
    def test_different_etag_triggers_full_pipeline(
        self,
        mock_conn,
        mock_get_all,
        mock_check_remote,
        mock_get_session,
        mock_rfc,
        mock_download,
        mock_ingest,
        mock_ingest_del,
        mock_upsert,
        mock_delete,
        mock_sync,
    ):
        """When remote etag differs → dataset is out_of_sync → full pipeline runs."""
        ds = _make_dataset_info("allcountries")
        mock_get_all.return_value = [ds]

        oos_record = _make_metadata_record(
            dataset="allcountries",
            out_of_sync=True,
            etag='"old-etag"',
            remote_etag='"new-etag"',
        )

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_out_of_sync.return_value = [oos_record]

            mock_client = MagicMock()
            mock_rfc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_rfc.return_value.__exit__ = MagicMock(return_value=False)
            mock_download.side_effect = [
                Path("/tmp/mod.txt"),
                Path("/tmp/del.txt"),
            ]

            check_and_apply_daily_updates()

        mock_check_remote.assert_called_once()
        assert mock_download.call_count == 2
        # Modifications ingested via _ingest_daily_file
        assert mock_ingest.call_count == 1
        # Deletions ingested via _ingest_deletions_file
        assert mock_ingest_del.call_count == 1
        mock_upsert.assert_called_once()
        mock_delete.assert_called_once()
        mock_sync.assert_called_once()

    @patch(f"{MODULE}._sync_metadata")
    @patch(f"{MODULE}._delete_from_dataset", return_value=1)
    @patch(f"{MODULE}._upsert_modifications", return_value=3)
    @patch(f"{MODULE}._ingest_deletions_file")
    @patch(f"{MODULE}._ingest_daily_file")
    @patch(f"{MODULE}._download_daily_file")
    @patch(f"{MODULE}.RemoteFileClient")
    @patch(f"{MODULE}.get_session")
    @patch(f"{MODULE}.check_remote_metadata")
    @patch(f"{MODULE}.get_all_datasets")
    @patch(f"{MODULE}._get_conn_params", return_value={"host": "localhost"})
    def test_newer_remote_last_modified_triggers_pipeline(
        self,
        mock_conn,
        mock_get_all,
        mock_check_remote,
        mock_get_session,
        mock_rfc,
        mock_download,
        mock_ingest,
        mock_ingest_del,
        mock_upsert,
        mock_delete,
        mock_sync,
    ):
        """When remote last_modified is newer → out_of_sync → pipeline runs."""
        ds = _make_dataset_info("cities5000")
        mock_get_all.return_value = [ds]

        oos_record = _make_metadata_record(
            dataset="cities5000",
            out_of_sync=True,
            etag='"same-etag"',
            remote_etag='"same-etag"',
        )
        # remote_last_modified is newer than local
        oos_record.remote_last_modified = datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc)
        oos_record.last_modified = datetime(2026, 3, 17, 12, 0, 0, tzinfo=timezone.utc)

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_out_of_sync.return_value = [oos_record]

            mock_client = MagicMock()
            mock_rfc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_rfc.return_value.__exit__ = MagicMock(return_value=False)
            mock_download.side_effect = [
                Path("/tmp/mod.txt"),
                Path("/tmp/del.txt"),
            ]

            check_and_apply_daily_updates()

        mock_upsert.assert_called_once()
        mock_delete.assert_called_once()
        mock_sync.assert_called_once()

    @patch(f"{MODULE}._sync_metadata")
    @patch(f"{MODULE}._delete_from_dataset", return_value=0)
    @patch(f"{MODULE}._upsert_modifications", return_value=0)
    @patch(f"{MODULE}._ingest_deletions_file")
    @patch(f"{MODULE}._ingest_daily_file")
    @patch(f"{MODULE}._download_daily_file")
    @patch(f"{MODULE}.RemoteFileClient")
    @patch(f"{MODULE}.get_session")
    @patch(f"{MODULE}.check_remote_metadata")
    @patch(f"{MODULE}.get_all_datasets")
    @patch(f"{MODULE}._get_conn_params", return_value={"host": "localhost"})
    def test_mixed_sync_only_updates_out_of_sync_datasets(
        self,
        mock_conn,
        mock_get_all,
        mock_check_remote,
        mock_get_session,
        mock_rfc,
        mock_download,
        mock_ingest,
        mock_ingest_del,
        mock_upsert,
        mock_delete,
        mock_sync,
    ):
        """When 3 datasets exist but only 1 has a changed etag, only that
        one is upserted/deleted/synced."""
        ds_all = _make_dataset_info("allcountries")
        ds_500 = _make_dataset_info("cities500")
        ds_5000 = _make_dataset_info("cities5000")
        mock_get_all.return_value = [ds_all, ds_500, ds_5000]

        # Only cities500 is out of sync (different etag)
        oos_record = _make_metadata_record(
            dataset="cities500",
            out_of_sync=True,
            etag='"old"',
            remote_etag='"new"',
        )

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_out_of_sync.return_value = [oos_record]

            mock_client = MagicMock()
            mock_rfc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_rfc.return_value.__exit__ = MagicMock(return_value=False)
            mock_download.side_effect = [Path("/tmp/mod.txt"), Path("/tmp/del.txt")]

            check_and_apply_daily_updates()

        # Only 1 dataset was out of sync → upsert/delete called once
        mock_upsert.assert_called_once()
        mock_delete.assert_called_once()
        # The sync only includes the out-of-sync dataset
        synced_datasets = mock_sync.call_args[0][0]
        assert len(synced_datasets) == 1
        assert synced_datasets[0].name == "cities500"

    @patch(f"{MODULE}._sync_metadata")
    @patch(f"{MODULE}._delete_from_dataset", return_value=0)
    @patch(f"{MODULE}._upsert_modifications", return_value=0)
    @patch(f"{MODULE}._ingest_deletions_file")
    @patch(f"{MODULE}._ingest_daily_file")
    @patch(f"{MODULE}._download_daily_file")
    @patch(f"{MODULE}.RemoteFileClient")
    @patch(f"{MODULE}.get_session")
    @patch(f"{MODULE}.check_remote_metadata")
    @patch(f"{MODULE}.get_all_datasets")
    @patch(f"{MODULE}._get_conn_params", return_value={"host": "localhost"})
    def test_sync_metadata_copies_remote_values_after_update(
        self,
        mock_conn,
        mock_get_all,
        mock_check_remote,
        mock_get_session,
        mock_rfc,
        mock_download,
        mock_ingest,
        mock_ingest_del,
        mock_upsert,
        mock_delete,
        mock_sync,
    ):
        """After applying updates, _sync_metadata is called so that
        local etag/last_modified are set to match remote (clearing out_of_sync)."""
        ds = _make_dataset_info("allcountries")
        mock_get_all.return_value = [ds]

        oos_record = _make_metadata_record(
            dataset="allcountries",
            out_of_sync=True,
            etag='"stale"',
            remote_etag='"fresh"',
        )

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_out_of_sync.return_value = [oos_record]

            mock_client = MagicMock()
            mock_rfc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_rfc.return_value.__exit__ = MagicMock(return_value=False)
            mock_download.side_effect = [Path("/tmp/mod.txt"), Path("/tmp/del.txt")]

            check_and_apply_daily_updates()

        # _sync_metadata was called with the out-of-sync datasets
        mock_sync.assert_called_once()
        synced = mock_sync.call_args[0][0]
        assert synced[0].name == "allcountries"

    @patch(f"{MODULE}._sync_metadata")
    @patch(f"{MODULE}._delete_from_dataset", return_value=0)
    @patch(f"{MODULE}._upsert_modifications", return_value=0)
    @patch(f"{MODULE}._ingest_daily_file")
    @patch(f"{MODULE}._download_daily_file")
    @patch(f"{MODULE}.RemoteFileClient")
    @patch(f"{MODULE}.get_session")
    @patch(f"{MODULE}.check_remote_metadata")
    @patch(f"{MODULE}.get_all_datasets")
    @patch(f"{MODULE}._get_conn_params", return_value={"host": "localhost"})
    def test_check_remote_metadata_called_with_all_datasets(
        self,
        mock_conn,
        mock_get_all,
        mock_check_remote,
        mock_get_session,
        mock_rfc,
        mock_download,
        mock_ingest,
        mock_upsert,
        mock_delete,
        mock_sync,
    ):
        """Step 1a always calls check_remote_metadata with ALL datasets,
        not just enabled ones, to detect remote changes."""
        ds_all = _make_dataset_info("allcountries")
        ds_500 = _make_dataset_info("cities500")
        ds_5000 = _make_dataset_info("cities5000")
        mock_get_all.return_value = [ds_all, ds_500, ds_5000]

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        with patch(f"{MODULE}.DatasetMetaDataRepository") as MockRepo:
            MockRepo.return_value.get_out_of_sync.return_value = []
            check_and_apply_daily_updates()

        mock_check_remote.assert_called_once_with([ds_all, ds_500, ds_5000])
