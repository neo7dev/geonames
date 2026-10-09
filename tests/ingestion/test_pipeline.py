"""Tests for geonames_server.ingestion.pipeline."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import psycopg2
import pytest

from geonames_server.database.models.cities15000 import Cities15000
from geonames_server.database.models.dataset_metadata import DatasetMetaData
from geonames_server.ingestion.downloader import DownloadResult
from geonames_server.ingestion.pipeline import (
    _get_conn_params,
    RowCountError,
    _load_and_swap,
    build_all_datasets,
    build_dataset,
    check_remote_metadata,
    seed_dataset_metadata,
    truncate_table,
)
from geonames_server.ingestion.registry import DatasetInfo
from geonames_server.ingestion.remote import RemoteFileMeta

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_dataset_info(**overrides) -> DatasetInfo:
    defaults = dict(
        name="cities15000",
        zip_file="cities15000.zip",
        txt_file="cities15000.txt",
        model=Cities15000,
    )
    defaults.update(overrides)
    return DatasetInfo(**defaults)


# ---------------------------------------------------------------------------
# _get_conn_params
# ---------------------------------------------------------------------------

class TestGetConnParams:
    def test_returns_dict_with_required_keys(self):
        params = _get_conn_params()
        assert isinstance(params, dict)
        for key in ("user", "password", "host", "port", "dbname"):
            assert key in params

    def test_values_come_from_settings(self):
        with patch("geonames_server.ingestion.pipeline.settings") as mock_settings:
            mock_settings.postgres_user = "u"
            mock_settings.postgres_password = "p"
            mock_settings.postgres_host = "h"
            mock_settings.postgres_port = 1234
            mock_settings.postgres_db = "d"
            params = _get_conn_params()
        assert params == {
            "user": "u",
            "password": "p",
            "host": "h",
            "port": 1234,
            "dbname": "d",
        }


# ---------------------------------------------------------------------------
# truncate_table
# ---------------------------------------------------------------------------

class TestTruncateTable:
    @patch("geonames_server.ingestion.pipeline._get_conn_params", return_value={"dbname": "testdb"})
    @patch("geonames_server.ingestion.pipeline.psycopg2.connect")
    def test_executes_truncate_sql(self, mock_connect, mock_params):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)

        # Patch as_string on Composed to avoid needing a real connection
        with patch("psycopg2.sql.Composed.as_string", return_value="TRUNCATE TABLE \"test_table\" CASCADE"):
            truncate_table("test_table")

        mock_connect.assert_called_once_with(dbname="testdb")
        mock_cur.execute.assert_called_once()
        executed_sql = mock_cur.execute.call_args[0][0]
        assert "TRUNCATE" in executed_sql.upper()


# ---------------------------------------------------------------------------
# seed_dataset_metadata
# ---------------------------------------------------------------------------

class TestSeedDatasetMetadata:
    @patch("geonames_server.ingestion.pipeline.DatasetMetaDataRepository")
    @patch("geonames_server.ingestion.pipeline.get_session")
    def test_creates_metadata_for_missing_datasets(self, mock_get_session, mock_repo_cls):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        mock_repo = MagicMock()
        mock_repo.get_by_dataset.return_value = None  # not yet seeded
        mock_repo_cls.return_value = mock_repo

        ds = _make_dataset_info()
        seed_dataset_metadata([ds])

        mock_repo.get_by_dataset.assert_called_once_with("cities15000")
        mock_repo.add.assert_called_once()
        added = mock_repo.add.call_args[0][0]
        assert isinstance(added, DatasetMetaData)
        assert added.dataset == "cities15000"
        assert added.out_of_sync is True
        assert added.needs_rebuild is True

    @patch("geonames_server.ingestion.pipeline.DatasetMetaDataRepository")
    @patch("geonames_server.ingestion.pipeline.get_session")
    def test_skips_existing_datasets(self, mock_get_session, mock_repo_cls):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        mock_repo = MagicMock()
        mock_repo.get_by_dataset.return_value = MagicMock()  # already exists
        mock_repo_cls.return_value = mock_repo

        ds = _make_dataset_info()
        seed_dataset_metadata([ds])

        mock_repo.get_by_dataset.assert_called_once_with("cities15000")
        mock_repo.add.assert_not_called()

    @patch("geonames_server.ingestion.pipeline.DatasetMetaDataRepository")
    @patch("geonames_server.ingestion.pipeline.get_session")
    def test_multiple_datasets(self, mock_get_session, mock_repo_cls):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        mock_repo = MagicMock()
        # First exists, second doesn't
        mock_repo.get_by_dataset.side_effect = [MagicMock(), None]
        mock_repo_cls.return_value = mock_repo

        ds1 = _make_dataset_info(name="cities15000")
        ds2 = _make_dataset_info(name="cities500", zip_file="cities500.zip", txt_file="cities500.txt")
        seed_dataset_metadata([ds1, ds2])

        assert mock_repo.get_by_dataset.call_count == 2
        assert mock_repo.add.call_count == 1


# ---------------------------------------------------------------------------
# check_remote_metadata
# ---------------------------------------------------------------------------

class TestCheckRemoteMetadata:
    @patch("geonames_server.ingestion.pipeline.settings")
    @patch("geonames_server.ingestion.pipeline.DatasetMetaDataRepository")
    @patch("geonames_server.ingestion.pipeline.get_session")
    @patch("geonames_server.ingestion.pipeline.RemoteFileClient")
    def test_updates_remote_etag_and_last_modified(
        self, mock_client_cls, mock_get_session, mock_repo_cls, mock_settings
    ):
        mock_settings.geonames_url = "https://example.com/dump/"

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        now = datetime(2025, 7, 1, tzinfo=timezone.utc)
        mock_meta = MagicMock()
        mock_meta.etag = '"abc123"'
        mock_meta.last_modified = now

        mock_client = MagicMock()
        mock_client.head.return_value = mock_meta
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_record = MagicMock()
        mock_record.dataset = "cities15000"
        mock_record.etag = None
        mock_record.remote_etag = None
        mock_record.last_modified = None
        mock_record.remote_last_modified = None

        mock_repo = MagicMock()
        mock_repo.get_by_dataset.return_value = mock_record
        mock_repo_cls.return_value = mock_repo

        ds = _make_dataset_info()
        check_remote_metadata([ds])

        mock_client.head.assert_called_once_with("https://example.com/dump/cities15000.zip")
        assert mock_record.remote_etag == '"abc123"'
        assert mock_record.remote_last_modified == now
        mock_session.commit.assert_called_once()

    @patch("geonames_server.ingestion.pipeline.settings")
    @patch("geonames_server.ingestion.pipeline.DatasetMetaDataRepository")
    @patch("geonames_server.ingestion.pipeline.get_session")
    @patch("geonames_server.ingestion.pipeline.RemoteFileClient")
    def test_uses_txt_file_when_no_zip(
        self, mock_client_cls, mock_get_session, mock_repo_cls, mock_settings
    ):
        mock_settings.geonames_url = "https://example.com/dump/"

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        mock_meta = MagicMock()
        mock_meta.etag = None
        mock_meta.last_modified = None

        mock_client = MagicMock()
        mock_client.head.return_value = mock_meta
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_repo = MagicMock()
        mock_repo.get_by_dataset.return_value = MagicMock(dataset="nozip", etag=None, remote_etag=None, last_modified=None, remote_last_modified=None)
        mock_repo_cls.return_value = mock_repo

        ds = DatasetInfo(name="nozip", zip_file="", txt_file="nozip.txt", model=Cities15000)
        check_remote_metadata([ds])

        mock_client.head.assert_called_once_with("https://example.com/dump/nozip.txt")

    @patch("geonames_server.ingestion.pipeline.settings")
    @patch("geonames_server.ingestion.pipeline.DatasetMetaDataRepository")
    @patch("geonames_server.ingestion.pipeline.get_session")
    @patch("geonames_server.ingestion.pipeline.RemoteFileClient")
    def test_skips_on_head_failure(
        self, mock_client_cls, mock_get_session, mock_repo_cls, mock_settings
    ):
        mock_settings.geonames_url = "https://example.com/dump/"

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_client.head.side_effect = Exception("network error")
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo

        ds = _make_dataset_info()
        # Should not raise
        check_remote_metadata([ds])

        mock_repo.get_by_dataset.assert_not_called()
        mock_session.commit.assert_not_called()

    @patch("geonames_server.ingestion.pipeline.settings")
    @patch("geonames_server.ingestion.pipeline.DatasetMetaDataRepository")
    @patch("geonames_server.ingestion.pipeline.get_session")
    @patch("geonames_server.ingestion.pipeline.RemoteFileClient")
    def test_skips_when_no_metadata_row(
        self, mock_client_cls, mock_get_session, mock_repo_cls, mock_settings
    ):
        mock_settings.geonames_url = "https://example.com/dump/"

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        mock_meta = MagicMock()
        mock_client = MagicMock()
        mock_client.head.return_value = mock_meta
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_repo = MagicMock()
        mock_repo.get_by_dataset.return_value = None  # no record
        mock_repo_cls.return_value = mock_repo

        ds = _make_dataset_info()
        check_remote_metadata([ds])

        mock_session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# build_dataset
# ---------------------------------------------------------------------------

class TestBuildDataset:
    @patch("geonames_server.ingestion.pipeline.DatasetMetaDataRepository")
    @patch("geonames_server.ingestion.pipeline.get_session")
    @patch("geonames_server.ingestion.pipeline._load_and_swap")
    @patch("geonames_server.ingestion.pipeline.Extractor")
    @patch("geonames_server.ingestion.pipeline.Downloader")
    @patch("geonames_server.ingestion.pipeline.settings")
    def test_full_pipeline_with_zip(
        self, mock_settings, mock_dl_cls, mock_ext_cls, mock_ingest, mock_get_session, mock_repo_cls
    ):
        mock_settings.cache_dir = Path("/tmp/cache")
        mock_settings.data_dir = Path("/tmp/data")
        mock_settings.geonames_url = "https://example.com/dump/"

        now = datetime(2025, 7, 1, tzinfo=timezone.utc)
        dl_result = DownloadResult(path=Path("/tmp/data/cities15000.zip"), etag='"etag1"', last_modified=now)

        mock_dl = MagicMock()
        mock_dl.download.return_value = dl_result
        mock_dl_cls.return_value.__enter__ = MagicMock(return_value=mock_dl)
        mock_dl_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_extractor = MagicMock()
        mock_ext_cls.return_value = mock_extractor

        # Make the txt file appear to exist
        txt_path = Path("/tmp/data/cities15000.txt")
        with patch.object(Path, "exists", return_value=True):
            mock_session = MagicMock()
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

            mock_metadata = MagicMock()
            mock_repo = MagicMock()
            mock_repo.get_by_dataset.return_value = mock_metadata
            mock_repo_cls.return_value = mock_repo

            ds = _make_dataset_info()
            build_dataset(ds, force=False)

        # Download called with correct URL
        mock_dl.download.assert_called_once_with(
            url="https://example.com/dump/cities15000.zip",
            file_name="cities15000.zip",
        )
        # Extractor called for zip
        mock_extractor.extract.assert_called_once()
        # Ingest called
        mock_ingest.assert_called_once()
        # Metadata updated
        assert mock_metadata.remote_etag == '"etag1"'
        assert mock_metadata.etag == '"etag1"'
        # dump built 2025-07-01 contains changes through 2025-06-30
        assert mock_metadata.last_applied_date == date(2025, 6, 30)
        mock_session.commit.assert_called_once()

    @patch("geonames_server.ingestion.pipeline.DatasetMetaDataRepository")
    @patch("geonames_server.ingestion.pipeline.get_session")
    @patch("geonames_server.ingestion.pipeline._load_and_swap")
    @patch("geonames_server.ingestion.pipeline.Downloader")
    @patch("geonames_server.ingestion.pipeline.settings")
    def test_no_extraction_for_txt_only(
        self, mock_settings, mock_dl_cls, mock_ingest, mock_get_session, mock_repo_cls
    ):
        mock_settings.cache_dir = Path("/tmp/cache")
        mock_settings.data_dir = Path("/tmp/data")
        mock_settings.geonames_url = "https://example.com/dump/"

        dl_result = DownloadResult(path=Path("/tmp/data/plain.txt"), etag=None, last_modified=None)

        mock_dl = MagicMock()
        mock_dl.download.return_value = dl_result
        mock_dl_cls.return_value.__enter__ = MagicMock(return_value=mock_dl)
        mock_dl_cls.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(Path, "exists", return_value=True):
            mock_session = MagicMock()
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

            mock_repo = MagicMock()
            mock_repo.get_by_dataset.return_value = MagicMock()
            mock_repo_cls.return_value = mock_repo

            ds = DatasetInfo(name="plain", zip_file="", txt_file="plain.txt", model=Cities15000)
            build_dataset(ds)

        mock_dl.download.assert_called_once_with(
            url="https://example.com/dump/plain.txt",
            file_name="plain.txt",
        )
        # Extractor should NOT have been called
        mock_ingest.assert_called_once()

    @patch("geonames_server.ingestion.pipeline.Downloader")
    @patch("geonames_server.ingestion.pipeline.settings")
    def test_raises_on_download_failure(self, mock_settings, mock_dl_cls):
        mock_settings.cache_dir = Path("/tmp/cache")
        mock_settings.data_dir = Path("/tmp/data")
        mock_settings.geonames_url = "https://example.com/dump/"

        mock_dl = MagicMock()
        mock_dl.download.side_effect = RuntimeError("download failed")
        mock_dl_cls.return_value.__enter__ = MagicMock(return_value=mock_dl)
        mock_dl_cls.return_value.__exit__ = MagicMock(return_value=False)

        ds = _make_dataset_info()
        with pytest.raises(RuntimeError, match="download failed"):
            build_dataset(ds)

    @patch("geonames_server.ingestion.pipeline.Extractor")
    @patch("geonames_server.ingestion.pipeline.Downloader")
    @patch("geonames_server.ingestion.pipeline.settings")
    def test_raises_on_extraction_failure(self, mock_settings, mock_dl_cls, mock_ext_cls):
        mock_settings.cache_dir = Path("/tmp/cache")
        mock_settings.data_dir = Path("/tmp/data")
        mock_settings.geonames_url = "https://example.com/dump/"

        mock_dl = MagicMock()
        mock_dl.download.return_value = DownloadResult(path=Path("/tmp/f.zip"))
        mock_dl_cls.return_value.__enter__ = MagicMock(return_value=mock_dl)
        mock_dl_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_ext_cls.return_value.extract.side_effect = RuntimeError("bad zip")

        ds = _make_dataset_info()
        with pytest.raises(RuntimeError, match="bad zip"):
            build_dataset(ds)

    @patch("geonames_server.ingestion.pipeline._load_and_swap")
    @patch("geonames_server.ingestion.pipeline.Downloader")
    @patch("geonames_server.ingestion.pipeline.settings")
    def test_raises_when_txt_file_missing(self, mock_settings, mock_dl_cls, mock_ingest):
        mock_settings.cache_dir = Path("/tmp/cache")
        mock_settings.data_dir = Path("/tmp/data")
        mock_settings.geonames_url = "https://example.com/dump/"

        mock_dl = MagicMock()
        mock_dl.download.return_value = DownloadResult(path=Path("/tmp/f.txt"))
        mock_dl_cls.return_value.__enter__ = MagicMock(return_value=mock_dl)
        mock_dl_cls.return_value.__exit__ = MagicMock(return_value=False)

        ds = DatasetInfo(name="nofile", zip_file="", txt_file="nofile.txt", model=Cities15000)
        with patch.object(Path, "exists", return_value=False):
            with pytest.raises(FileNotFoundError, match="Extracted file not found"):
                build_dataset(ds)

    @patch("geonames_server.ingestion.pipeline._load_and_swap")
    @patch("geonames_server.ingestion.pipeline.Downloader")
    @patch("geonames_server.ingestion.pipeline.settings")
    def test_raises_on_ingest_failure(self, mock_settings, mock_dl_cls, mock_ingest):
        mock_settings.cache_dir = Path("/tmp/cache")
        mock_settings.data_dir = Path("/tmp/data")
        mock_settings.geonames_url = "https://example.com/dump/"

        mock_dl = MagicMock()
        mock_dl.download.return_value = DownloadResult(path=Path("/tmp/f.txt"))
        mock_dl_cls.return_value.__enter__ = MagicMock(return_value=mock_dl)
        mock_dl_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_ingest.side_effect = RuntimeError("ingest boom")

        ds = DatasetInfo(name="boom", zip_file="", txt_file="boom.txt", model=Cities15000)
        with patch.object(Path, "exists", return_value=True):
            with pytest.raises(RuntimeError, match="ingest boom"):
                build_dataset(ds)


# ---------------------------------------------------------------------------
# build_all_datasets
# ---------------------------------------------------------------------------

class TestBuildAllDatasets:
    @patch("geonames_server.ingestion.pipeline.build_dataset")
    @patch("geonames_server.ingestion.pipeline.DatasetMetaDataRepository")
    @patch("geonames_server.ingestion.pipeline.get_session")
    @patch("geonames_server.ingestion.pipeline.get_enabled_datasets")
    @patch("geonames_server.ingestion.pipeline.settings")
    def test_builds_datasets_needing_rebuild(
        self, mock_settings, mock_get_enabled, mock_get_session, mock_repo_cls, mock_build
    ):
        mock_settings.enabled_datasets = ["cities15000"]

        ds = _make_dataset_info()
        mock_get_enabled.return_value = [ds]

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        mock_metadata = MagicMock()
        mock_metadata.needs_rebuild = True
        mock_repo = MagicMock()
        mock_repo.get_by_dataset.return_value = mock_metadata
        mock_repo_cls.return_value = mock_repo

        build_all_datasets()

        mock_build.assert_called_once_with(ds, force=True)

    @patch("geonames_server.ingestion.pipeline.build_dataset")
    @patch("geonames_server.ingestion.pipeline.DatasetMetaDataRepository")
    @patch("geonames_server.ingestion.pipeline.get_session")
    @patch("geonames_server.ingestion.pipeline.get_enabled_datasets")
    @patch("geonames_server.ingestion.pipeline.settings")
    def test_skips_datasets_not_needing_rebuild(
        self, mock_settings, mock_get_enabled, mock_get_session, mock_repo_cls, mock_build
    ):
        mock_settings.enabled_datasets = ["cities15000"]

        ds = _make_dataset_info()
        mock_get_enabled.return_value = [ds]

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        mock_metadata = MagicMock()
        mock_metadata.needs_rebuild = False
        mock_repo = MagicMock()
        mock_repo.get_by_dataset.return_value = mock_metadata
        mock_repo_cls.return_value = mock_repo

        build_all_datasets()

        mock_build.assert_not_called()

    @patch("geonames_server.ingestion.pipeline.build_dataset")
    @patch("geonames_server.ingestion.pipeline.DatasetMetaDataRepository")
    @patch("geonames_server.ingestion.pipeline.get_session")
    @patch("geonames_server.ingestion.pipeline.get_enabled_datasets")
    @patch("geonames_server.ingestion.pipeline.settings")
    def test_builds_when_metadata_is_none(
        self, mock_settings, mock_get_enabled, mock_get_session, mock_repo_cls, mock_build
    ):
        mock_settings.enabled_datasets = ["cities15000"]

        ds = _make_dataset_info()
        mock_get_enabled.return_value = [ds]

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        mock_repo = MagicMock()
        mock_repo.get_by_dataset.return_value = None  # no metadata yet
        mock_repo_cls.return_value = mock_repo

        build_all_datasets()

        mock_build.assert_called_once_with(ds, force=True)

    @patch("geonames_server.ingestion.pipeline.build_dataset")
    @patch("geonames_server.ingestion.pipeline.DatasetMetaDataRepository")
    @patch("geonames_server.ingestion.pipeline.get_session")
    @patch("geonames_server.ingestion.pipeline.get_enabled_datasets")
    @patch("geonames_server.ingestion.pipeline.settings")
    def test_continues_on_individual_build_failure(
        self, mock_settings, mock_get_enabled, mock_get_session, mock_repo_cls, mock_build
    ):
        mock_settings.enabled_datasets = ["cities15000", "cities500"]

        ds1 = _make_dataset_info(name="cities15000")
        ds2 = _make_dataset_info(name="cities500", zip_file="cities500.zip", txt_file="cities500.txt")
        mock_get_enabled.return_value = [ds1, ds2]

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        mock_repo = MagicMock()
        mock_repo.get_by_dataset.return_value = None  # both need build
        mock_repo_cls.return_value = mock_repo

        # First build fails, second succeeds
        mock_build.side_effect = [RuntimeError("boom"), None]

        # Should not raise — failures are logged
        build_all_datasets()

        assert mock_build.call_count == 2

    @patch("geonames_server.ingestion.pipeline.build_dataset")
    @patch("geonames_server.ingestion.pipeline.DatasetMetaDataRepository")
    @patch("geonames_server.ingestion.pipeline.get_session")
    @patch("geonames_server.ingestion.pipeline.get_enabled_datasets")
    @patch("geonames_server.ingestion.pipeline.settings")
    def test_empty_enabled_no_rebuild(
        self, mock_settings, mock_get_enabled, mock_get_session, mock_repo_cls, mock_build
    ):
        mock_settings.enabled_datasets = []
        mock_get_enabled.return_value = []

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo

        build_all_datasets()

        mock_build.assert_not_called()


# ---------------------------------------------------------------------------
# _truncate_and_ingest
# ---------------------------------------------------------------------------

class TestLoadAndSwap:
    PB = "geonames_server.rich_console.ingestion_progressbar"

    @staticmethod
    def _conn(counts=(5, 5), copy_error=None):
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cur.fetchone.side_effect = [(counts[0],), (counts[1],), ("cities15000_pkey",)]
        cur.fetchall.return_value = []
        if copy_error:
            cur.copy_expert.side_effect = copy_error
        return conn, cur

    @staticmethod
    def _sql(cur):
        return [
            c.args[0] if isinstance(c.args[0], str) else repr(c.args[0])
            for c in cur.execute.call_args_list
        ]

    def _run(self, conn, tmp_path):
        txt = tmp_path / "test.txt"
        txt.write_text("1\tTestCity\n")
        with (
            patch(f"{self.PB}._ProgressFileWrapper") as wrapper_cls,
            patch(f"{self.PB}.IngestionProgressBar", new=_MockProgressBarClass),
            patch("geonames_server.ingestion.pipeline._get_conn_params", return_value={}),
            patch("geonames_server.ingestion.pipeline.psycopg2.connect", return_value=conn),
        ):
            try:
                _load_and_swap(Cities15000, "cities15000", txt)
            finally:
                self.wrapper = wrapper_cls.return_value

    def test_loads_into_staging_then_swaps_in_order(self, tmp_path):
        conn, cur = self._conn()
        self._run(conn, tmp_path)

        statements = self._sql(cur)
        order = [
            next(i for i, q in enumerate(statements) if needle in q)
            for needle in (
                "CREATE TABLE",
                "ADD PRIMARY KEY",
                "lock_timeout",
                "RENAME TO",
                "DROP TABLE",
                "RENAME CONSTRAINT",
            )
        ]
        assert order == sorted(order)
        assert 'COPY "cities15000_new"' in cur.copy_expert.call_args[0][0]
        assert not any("TRUNCATE" in q for q in statements)
        assert conn.autocommit is False
        conn.commit.assert_called_once()
        conn.close.assert_called_once()
        self.wrapper.close.assert_called_once()

    def test_refuses_swap_when_load_is_too_small(self, tmp_path):
        conn, cur = self._conn(counts=(10, 100))
        with pytest.raises(RowCountError):
            self._run(conn, tmp_path)

        assert not any("RENAME" in q for q in self._sql(cur))
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()
        conn.close.assert_called_once()

    def test_refuses_swap_when_load_is_empty(self, tmp_path):
        conn, _ = self._conn(counts=(0, 0))
        with pytest.raises(RowCountError):
            self._run(conn, tmp_path)

    def test_rollback_and_close_on_error(self, tmp_path):
        conn, _ = self._conn(copy_error=RuntimeError("copy failed"))
        with pytest.raises(RuntimeError, match="copy failed"):
            self._run(conn, tmp_path)

        conn.rollback.assert_called_once()
        conn.close.assert_called_once()
        self.wrapper.close.assert_called_once()

    def test_connect_failure_propagates_original_error(self, tmp_path):
        txt = tmp_path / "test.txt"
        txt.write_text("x\n")
        with (
            patch(f"{self.PB}._ProgressFileWrapper"),
            patch(f"{self.PB}.IngestionProgressBar", new=_MockProgressBarClass),
            patch("geonames_server.ingestion.pipeline._get_conn_params", return_value={}),
            patch(
                "geonames_server.ingestion.pipeline.psycopg2.connect",
                side_effect=psycopg2.OperationalError("down"),
            ),
        ):
            with pytest.raises(psycopg2.OperationalError, match="down"):
                _load_and_swap(Cities15000, "cities15000", txt)


# ---------------------------------------------------------------------------
# Helpers for mocking IngestionProgressBar context manager
# ---------------------------------------------------------------------------

class _MockProgressBarClass:
    """Factory that returns a class whose instances are no-op context managers."""
    def __init__(self, total, description):
        pass

    def __enter__(self):
        return MagicMock()

    def __exit__(self, *exc):
        return False
