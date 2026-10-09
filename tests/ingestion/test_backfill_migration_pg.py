"""Integration test for the last_applied_date backfill migration.

Needs TEST_PG_DSN; uses a TEMP table, so nothing persistent is touched.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import psycopg2
import pytest

DSN = os.environ.get("TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_PG_DSN not set")

MIGRATION = (
    Path(__file__).parents[2]
    / "migrations/versions/d4a9b2c8f1e7_backfill_last_applied_date.py"
)


def _backfill_sql() -> str:
    spec = importlib.util.spec_from_file_location("backfill_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.BACKFILL_SQL


def test_backfill_fills_only_null_markers_of_synced_datasets():
    conn = psycopg2.connect(DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TEMP TABLE dataset_metadata ("
                "dataset text PRIMARY KEY, last_modified timestamptz, "
                "last_applied_date date)"
            )
            cur.execute(
                "INSERT INTO dataset_metadata VALUES "
                "('synced', '2026-03-18 00:30:00+00', NULL), "
                "('late_evening_local', '2026-03-17 23:30:00-05', NULL), "
                "('never_built', NULL, NULL), "
                "('already_set', '2026-03-18 12:00:00+00', '2026-03-10')"
            )
            cur.execute(_backfill_sql())
            cur.execute("SELECT dataset, last_applied_date FROM dataset_metadata")
            result = {name: str(day) for name, day in cur.fetchall()}
    finally:
        conn.rollback()
        conn.close()

    assert result == {
        "synced": "2026-03-17",
        # 2026-03-17 23:30-05 is 2026-03-18 04:30 UTC, so the UTC date minus one
        "late_evening_local": "2026-03-17",
        "never_built": "None",
        "already_set": "2026-03-10",
    }
