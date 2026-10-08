from __future__ import annotations

import os
from pathlib import Path  # noqa: F401
from unittest.mock import MagicMock, call, patch  # noqa: F401

import pytest

# Fix ENABLED_DATASETS env var format for pydantic (needs JSON list)
_orig_datasets = os.environ.get("ENABLED_DATASETS", "")
if _orig_datasets and not _orig_datasets.startswith("["):
    os.environ["ENABLED_DATASETS"] = f'["{_orig_datasets}"]'

from geonames_server.ingestion.ingestor import Ingestor  # noqa: E402

# Restore original value
if _orig_datasets:
    os.environ["ENABLED_DATASETS"] = _orig_datasets


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_model(table_name: str = "test_table", columns: list[str] | None = None):
    """Build a mock SQLAlchemy model with a __table__ that has .name and .columns."""
    columns = columns or ["id", "name", "value"]
    mock_cols = []
    for col_name in columns:
        col = MagicMock()
        col.name = col_name
        mock_cols.append(col)

    mock_table = MagicMock()
    mock_table.name = table_name
    mock_table.columns = mock_cols

    model = MagicMock()
    model.__table__ = mock_table
    return model


# ---------------------------------------------------------------------------
# Ingestor.__init__
# ---------------------------------------------------------------------------


class TestIngestorInit:
    def test_uses_custom_conn_info(self):
        custom = {
            "user": "u",
            "password": "p",
            "host": "h",
            "port": "1234",
            "dbname": "d",
        }
        ing = Ingestor(conn_info=custom)
        assert ing.conn_params == custom

    @patch("geonames_server.ingestion.ingestor.settings")
    def test_defaults_to_settings(self, mock_settings):
        mock_settings.postgres_user = "su"
        mock_settings.postgres_password = "sp"
        mock_settings.postgres_host = "sh"
        mock_settings.postgres_port = "5432"
        mock_settings.postgres_db = "sd"

        ing = Ingestor()
        assert ing.conn_params["user"] == "su"
        assert ing.conn_params["dbname"] == "sd"


# ---------------------------------------------------------------------------
# Ingestor.ingest – error cases
# ---------------------------------------------------------------------------


class TestIngestErrors:
    def test_raises_file_not_found(self, tmp_path):
        ing = Ingestor(
            conn_info={
                "user": "x",
                "password": "x",
                "host": "x",
                "port": "x",
                "dbname": "x",
            }
        )
        with pytest.raises(FileNotFoundError, match="Input file not found"):
            ing.ingest(tmp_path / "nonexistent.tsv", _make_mock_model())

    @patch("geonames_server.ingestion.ingestor.sql")
    @patch("geonames_server.ingestion.ingestor.IngestionProgressBar")
    @patch("geonames_server.ingestion.ingestor.psycopg2")
    def test_raises_on_connection_error(
        self, mock_psycopg2, mock_pb_cls, mock_sql, tmp_path
    ):
        # Create a valid file
        tsv = tmp_path / "data.tsv"
        tsv.write_text("1\tAlice\t100\n")

        # Mock progress bar
        mock_pb = MagicMock()
        mock_pb.__enter__ = MagicMock(return_value=mock_pb)
        mock_pb.__exit__ = MagicMock(return_value=False)
        mock_pb_cls.return_value = mock_pb

        # Simulate connection failure
        mock_psycopg2.connect.side_effect = Exception("connection refused")

        ing = Ingestor(
            conn_info={
                "user": "x",
                "password": "x",
                "host": "x",
                "port": "x",
                "dbname": "x",
            }
        )
        with pytest.raises(Exception, match="connection refused"):
            ing.ingest(tsv, _make_mock_model())


# ---------------------------------------------------------------------------
# Ingestor.ingest – success cases
# ---------------------------------------------------------------------------


class TestIngest:
    @patch("geonames_server.ingestion.ingestor.sql")
    @patch("geonames_server.ingestion.ingestor._ProgressFileWrapper")
    @patch("geonames_server.ingestion.ingestor.IngestionProgressBar")
    @patch("geonames_server.ingestion.ingestor.psycopg2")
    def test_calls_copy_expert(
        self, mock_psycopg2, mock_pb_cls, mock_wrapper_cls, mock_sql, tmp_path
    ):
        tsv = tmp_path / "data.tsv"
        tsv.write_text("1\tAlice\t100\n")

        mock_pb = MagicMock()
        mock_pb.__enter__ = MagicMock(return_value=mock_pb)
        mock_pb.__exit__ = MagicMock(return_value=False)
        mock_pb_cls.return_value = mock_pb

        mock_wrapper = MagicMock()
        mock_wrapper_cls.return_value = mock_wrapper

        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur

        mock_psycopg2.connect.return_value = mock_conn

        model = _make_mock_model("my_table", ["id", "name", "value"])

        ing = Ingestor(
            conn_info={
                "user": "u",
                "password": "p",
                "host": "h",
                "port": "5432",
                "dbname": "d",
            }
        )
        ing.ingest(tsv, model)

        # copy_expert should have been called once (into temp staging table)
        mock_cur.copy_expert.assert_called_once()
        # wrapper passed as second arg
        assert mock_cur.copy_expert.call_args[0][1] is mock_wrapper
        # CREATE TEMP TABLE + MERGE
        assert mock_cur.execute.call_count == 2
        # wrapper.close was called
        mock_wrapper.close.assert_called_once()

    @patch("geonames_server.ingestion.ingestor.sql")
    @patch("geonames_server.ingestion.ingestor._ProgressFileWrapper")
    @patch("geonames_server.ingestion.ingestor.IngestionProgressBar")
    @patch("geonames_server.ingestion.ingestor.psycopg2")
    def test_progress_bar_receives_file_size(
        self, mock_psycopg2, mock_pb_cls, mock_wrapper_cls, mock_sql, tmp_path
    ):
        content = "1\tAlice\t100\n2\tBob\t200\n"
        tsv = tmp_path / "data.tsv"
        tsv.write_text(content)

        mock_pb = MagicMock()
        mock_pb.__enter__ = MagicMock(return_value=mock_pb)
        mock_pb.__exit__ = MagicMock(return_value=False)
        mock_pb_cls.return_value = mock_pb

        mock_wrapper = MagicMock()
        mock_wrapper_cls.return_value = mock_wrapper

        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur

        mock_psycopg2.connect.return_value = mock_conn

        ing = Ingestor(
            conn_info={
                "user": "u",
                "password": "p",
                "host": "h",
                "port": "5432",
                "dbname": "d",
            }
        )
        ing.ingest(tsv, _make_mock_model())

        # IngestionProgressBar should receive the file's byte size
        mock_pb_cls.assert_called_once()
        assert mock_pb_cls.call_args[0][0] == tsv.stat().st_size

    @patch("geonames_server.ingestion.ingestor.sql")
    @patch("geonames_server.ingestion.ingestor._ProgressFileWrapper")
    @patch("geonames_server.ingestion.ingestor.IngestionProgressBar")
    @patch("geonames_server.ingestion.ingestor.psycopg2")
    def test_sql_uses_table_and_columns(
        self, mock_psycopg2, mock_pb_cls, mock_wrapper_cls, mock_sql, tmp_path
    ):
        tsv = tmp_path / "data.tsv"
        tsv.write_text("1\tval\n")

        mock_pb = MagicMock()
        mock_pb.__enter__ = MagicMock(return_value=mock_pb)
        mock_pb.__exit__ = MagicMock(return_value=False)
        mock_pb_cls.return_value = mock_pb

        mock_wrapper = MagicMock()
        mock_wrapper_cls.return_value = mock_wrapper

        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur

        mock_psycopg2.connect.return_value = mock_conn

        model = _make_mock_model("tbl", ["col_a", "col_b"])

        ing = Ingestor(
            conn_info={
                "user": "u",
                "password": "p",
                "host": "h",
                "port": "5432",
                "dbname": "d",
            }
        )
        ing.ingest(tsv, model)

        copy_sql = mock_cur.copy_expert.call_args[0][0]
        assert 'COPY "tmp_tbl_ingest_stage"' in copy_sql
        assert '"col_a", "col_b"' in copy_sql

    @patch("geonames_server.ingestion.ingestor.sql")
    @patch("geonames_server.ingestion.ingestor._ProgressFileWrapper")
    @patch("geonames_server.ingestion.ingestor.IngestionProgressBar")
    @patch("geonames_server.ingestion.ingestor.psycopg2")
    def test_wrapper_closed_on_copy_error(
        self, mock_psycopg2, mock_pb_cls, mock_wrapper_cls, mock_sql, tmp_path
    ):
        tsv = tmp_path / "data.tsv"
        tsv.write_text("1\tbad\n")

        mock_pb = MagicMock()
        mock_pb.__enter__ = MagicMock(return_value=mock_pb)
        mock_pb.__exit__ = MagicMock(return_value=False)
        mock_pb_cls.return_value = mock_pb

        mock_wrapper = MagicMock()
        mock_wrapper_cls.return_value = mock_wrapper

        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.copy_expert.side_effect = Exception("COPY failed")

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur

        mock_psycopg2.connect.return_value = mock_conn

        ing = Ingestor(
            conn_info={
                "user": "u",
                "password": "p",
                "host": "h",
                "port": "5432",
                "dbname": "d",
            }
        )
        with pytest.raises(Exception, match="COPY failed"):
            ing.ingest(tsv, _make_mock_model())

        # wrapper must still be closed even on error
        mock_wrapper.close.assert_called_once()

    @patch("geonames_server.ingestion.ingestor.sql")
    @patch("geonames_server.ingestion.ingestor._ProgressFileWrapper")
    @patch("geonames_server.ingestion.ingestor.IngestionProgressBar")
    @patch("geonames_server.ingestion.ingestor.psycopg2")
    def test_connects_with_provided_conn_params(
        self, mock_psycopg2, mock_pb_cls, mock_wrapper_cls, mock_sql, tmp_path
    ):
        tsv = tmp_path / "data.tsv"
        tsv.write_text("1\tx\n")

        mock_pb = MagicMock()
        mock_pb.__enter__ = MagicMock(return_value=mock_pb)
        mock_pb.__exit__ = MagicMock(return_value=False)
        mock_pb_cls.return_value = mock_pb

        mock_wrapper = MagicMock()
        mock_wrapper_cls.return_value = mock_wrapper

        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur

        mock_psycopg2.connect.return_value = mock_conn

        conn_info = {
            "user": "myuser",
            "password": "mypass",
            "host": "myhost",
            "port": "9999",
            "dbname": "mydb",
        }
        Ingestor(conn_info=conn_info).ingest(tsv, _make_mock_model())

        mock_psycopg2.connect.assert_called_once_with(**conn_info)
