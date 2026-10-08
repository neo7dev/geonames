"""Daily update pipeline for GeoNames datasets.

Workflow
-------
1a. HEAD-check each dataset's zip (or txt) file to compare etag &
    last-modified against the stored values.  If the remote file has
    changed the dataset record is marked *out_of_sync* automatically.
1b. If any dataset is out of sync, check whether yesterday's
    ``modifications-YYYY-MM-DD.txt`` and ``deletes-YYYY-MM-DD.txt``
    exist on the remote server.  If they do, download them and COPY
    them into the ``modifications`` / ``deletions`` staging tables.
2a. Use SQL to UPSERT from ``modifications`` and DELETE via
    ``deletions`` into each out-of-sync dataset table, filtered to
    yesterday's date only.
2b. Population-based filtering per dataset:
    - allcountries  → all modifications, all deletions
    - cities500     → population ≥ 500, all deletions (if record exists)
    - cities1000    → population ≥ 1 000, all deletions (if record exists)
    - cities5000    → population ≥ 5 000, all deletions (if record exists)
    - cities15000   → population ≥ 15 000, all deletions (if record exists)
3.  Copy each dataset's remote etag/last-modified into the local
    fields so ``out_of_sync`` is cleared by the model event listener.
"""

from __future__ import annotations

import io
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TextIO

import psycopg2
from psycopg2 import sql

from geonames_server.config.config import settings
from geonames_server.database.models.allcountries import AllCountries
from geonames_server.database.models.deletions import Deletions
from geonames_server.database.models.modifications import Modifications
from geonames_server.database.repository.sync.dataset_metadata_repository import (
    DatasetMetaDataRepository,
)
from geonames_server.database.session import get_session
from geonames_server.ingestion.downloader import Downloader
from geonames_server.ingestion.pipeline import _get_conn_params, check_remote_metadata
from geonames_server.ingestion.registry import DatasetInfo, get_all_datasets
from geonames_server.ingestion.remote import RemoteFileClient
from geonames_server.log.mylogger import get_logger

log = get_logger()

# Column names shared by the modifications staging table and every dataset
# table (they all inherit GeoBase).
GEOBASE_COLUMNS: list[str] = [c.name for c in AllCountries.__table__.columns]

# Minimum population a modifications row must have to be applied to a
# given dataset table.  0 means "no filter" (all rows are applied).
POPULATION_THRESHOLDS: dict[str, int] = {
    "allcountries": 0,
    "cities500": 500,
    "cities1000": 1000,
    "cities5000": 5000,
    "cities15000": 15000,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _yesterday() -> date:
    return date.today() - timedelta(days=1)


def _download_daily_file(client: RemoteFileClient, file_name: str) -> Path | None:
    """Check if a daily file exists on the remote server and download it.

    Returns the local path on success, ``None`` if the file does not exist.
    """
    url = settings.geonames_url + file_name

    if not client.exists(url):
        log.info(f"Daily file not available: {url}")
        return None

    try:
        with Downloader(
            cache_dir=settings.cache_dir, data_dir=settings.data_dir, force=True
        ) as dl:
            return dl.download(url=url, file_name=file_name).path
    except Exception:
        log.exception(f"Failed to download daily file: {url}")
        return None


def _copy_into_temp_table(
    cur,
    target_table: str,
    temp_table: str,
    columns: list[str],
    source: TextIO,
) -> None:
    """COPY source rows into a temporary table that mirrors *target_table*."""
    create_temp_sql = sql.SQL(
        "CREATE TEMP TABLE {temp} (LIKE {target} INCLUDING DEFAULTS) ON COMMIT DROP"
    ).format(
        temp=sql.Identifier(temp_table),
        target=sql.Identifier(target_table),
    )
    cur.execute(create_temp_sql)

    quoted_columns = ", ".join(f'"{col}"' for col in columns)
    copy_sql = (
        f'COPY "{temp_table}" ({quoted_columns}) '
        "FROM STDIN "
        "WITH (FORMAT csv, DELIMITER E'\\t', NULL '', QUOTE E'\\b', ESCAPE E'\\b')"
    )
    cur.copy_expert(copy_sql, source)


def _merge_unique_rows(
    cur,
    temp_table: str,
    target_table: str,
    columns: list[str],
    conflict_columns: list[str],
    update_columns: list[str],
) -> None:
    """Insert/update from temp rows while skipping duplicate incoming keys."""
    merge_sql = sql.SQL(
        "WITH staged AS ("
        "  SELECT {columns}, "
        "         COUNT(*) OVER (PARTITION BY {conflict_columns}) AS duplicate_count "
        "  FROM {temp}"
        ") "
        "INSERT INTO {target} ({columns}) "
        "SELECT {columns} FROM staged WHERE duplicate_count = 1 "
        "ON CONFLICT ({conflict_columns}) DO UPDATE SET {updates}"
    ).format(
        temp=sql.Identifier(temp_table),
        target=sql.Identifier(target_table),
        columns=sql.SQL(", ").join(map(sql.Identifier, columns)),
        conflict_columns=sql.SQL(", ").join(map(sql.Identifier, conflict_columns)),
        updates=sql.SQL(", ").join(
            sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(c))
            for c in update_columns
        ),
    )
    cur.execute(merge_sql)


def _ingest_daily_file(file_path: Path, model) -> None:
    """COPY into temp staging, then merge unique rows into the target table.

    Incoming duplicate keys are skipped. Existing target rows are updated.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {file_path}")

    table = model.__table__
    columns = [c.name for c in table.columns]
    temp_table = f"tmp_{table.name}_daily_stage"

    # GeoBase-derived daily tables use a composite key in daily updates.
    conflict_columns = ["geonameid", "modification_date"]
    update_columns = [
        c for c in columns if c not in set(conflict_columns)
    ]

    conn_params = _get_conn_params()
    with psycopg2.connect(**conn_params) as conn:
        with conn.cursor() as cur:
            with open(path, encoding="utf-8") as fh:
                _copy_into_temp_table(
                    cur,
                    table.name,
                    temp_table,
                    columns,
                    fh,
                )
            _merge_unique_rows(
                cur,
                temp_table=temp_table,
                target_table=table.name,
                columns=columns,
                conflict_columns=conflict_columns,
                update_columns=update_columns,
            )


def _ingest_deletions_file(file_path: Path, deletion_date: date) -> None:
    """COPY a daily deletes file into the ``deletions`` staging table.

    The remote ``deletes-YYYY-MM-DD.txt`` contains only three columns
    (geonameid, name, reason) but the ``deletions`` table has a leading
    ``deletion_date`` column.  This function prepends the date (derived
    from the filename / ``yesterday``) to every row before COPYing.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {file_path}")

    date_prefix = deletion_date.isoformat() + "\t"
    buf = io.StringIO()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            buf.write(date_prefix + line)
    buf.seek(0)

    table = Deletions.__table__
    columns = [c.name for c in table.columns]
    temp_table = "tmp_deletions_daily_stage"

    conn_params = _get_conn_params()
    with psycopg2.connect(**conn_params) as conn:
        with conn.cursor() as cur:
            _copy_into_temp_table(
                cur,
                table.name,
                temp_table,
                columns,
                buf,
            )
            _merge_unique_rows(
                cur,
                temp_table=temp_table,
                target_table=table.name,
                columns=columns,
                conflict_columns=["deletion_date", "geonameid"],
                update_columns=["name", "reason"],
            )


# ---------------------------------------------------------------------------
# Step 2 – apply staged rows to dataset tables
# ---------------------------------------------------------------------------


def _upsert_modifications(
    conn_params: dict,
    target_table: str,
    target_date: date,
    population_threshold: int,
) -> int:
    """UPSERT rows from the ``modifications`` staging table into *target_table*.

    Only rows whose ``modification_date`` equals *target_date* are
    considered.  For city datasets a ``population >= threshold`` filter
    is applied; ``allcountries`` uses threshold 0 (no filter).

    Returns the number of rows affected.
    """
    update_cols = [c for c in GEOBASE_COLUMNS if c != "geonameid"]
    cols_sql = sql.SQL(", ").join(map(sql.Identifier, GEOBASE_COLUMNS))

    where_parts = [sql.SQL("modification_date = %s")]
    params: list = [target_date]

    if population_threshold > 0:
        where_parts.append(sql.SQL("COALESCE(population, 0) >= %s"))
        params.append(population_threshold)

    upsert_sql = sql.SQL(
        "INSERT INTO {target} ({columns}) "
        "SELECT {columns} FROM {source} WHERE {where} "
        "ON CONFLICT (geonameid) DO UPDATE SET {updates}"
    ).format(
        target=sql.Identifier(target_table),
        source=sql.Identifier("modifications"),
        columns=cols_sql,
        where=sql.SQL(" AND ").join(where_parts),
        updates=sql.SQL(", ").join(
            sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(c))
            for c in update_cols
        ),
    )

    with psycopg2.connect(**conn_params) as conn:
        with conn.cursor() as cur:
            cur.execute(upsert_sql, params)
            return cur.rowcount


def _delete_from_dataset(
    conn_params: dict,
    target_table: str,
    target_date: date,
) -> int:
    """DELETE rows from *target_table* whose ``geonameid`` appears in the
    ``deletions`` staging table for *target_date*.

    Only rows that actually exist in *target_table* are removed.
    Returns the number of rows deleted.
    """
    delete_sql = sql.SQL(
        "DELETE FROM {target} "
        "WHERE geonameid IN "
        "(SELECT geonameid FROM {source} WHERE deletion_date = %s)"
    ).format(
        target=sql.Identifier(target_table),
        source=sql.Identifier("deletions"),
    )

    with psycopg2.connect(**conn_params) as conn:
        with conn.cursor() as cur:
            cur.execute(delete_sql, [target_date])
            return cur.rowcount


# ---------------------------------------------------------------------------
# Step 3 – sync local metadata to remote values
# ---------------------------------------------------------------------------


def _sync_metadata(datasets: list[DatasetInfo]) -> None:
    """Copy each dataset's remote etag / last-modified into the local fields.

    The ``before_update`` event listener on :class:`DatasetMetaData`
    recomputes ``out_of_sync`` (becomes ``False`` when local matches
    remote).
    """
    now = datetime.now(timezone.utc)
    with get_session() as session:
        repo = DatasetMetaDataRepository(session)
        for ds in datasets:
            record = repo.get_by_dataset(ds.name)
            if record is None:
                continue
            record.etag = record.remote_etag
            record.last_modified = now
            session.commit()
            log.info(
                f"[{ds.name}] Metadata synced — out_of_sync={record.out_of_sync}"
            )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def check_and_apply_daily_updates() -> None:
    """Main daily-update function called by the scheduler.

    1a. HEAD-check all dataset zip/txt files to detect remote changes.
    1b. If any dataset is out of sync, download yesterday's daily files
        and COPY them into the modifications / deletions staging tables.
    2.  UPSERT modifications and DELETE deletions into each out-of-sync
        dataset table (with population-based filtering).
    3.  Sync local metadata to remote values (auto-clears out_of_sync).
    """
    all_datasets = get_all_datasets()
    yesterday = _yesterday()

    log.info(f"Starting daily update check for {yesterday}")

    # --- Step 1a: HEAD-check remote metadata for all datasets -----------
    check_remote_metadata(all_datasets)

    # --- Step 1b: Determine which datasets are out of sync --------------
    with get_session() as session:
        repo = DatasetMetaDataRepository(session)
        out_of_sync_records = repo.get_out_of_sync()

    if not out_of_sync_records:
        log.info("All datasets are in sync — nothing to do")
        return

    out_of_sync_names = {r.dataset for r in out_of_sync_records}
    log.info(f"Out-of-sync datasets: {sorted(out_of_sync_names)}")

    # Check for yesterday's daily files and download them
    mod_file = f"modifications-{yesterday.isoformat()}.txt"
    del_file = f"deletes-{yesterday.isoformat()}.txt"

    with RemoteFileClient() as client:
        mod_path = _download_daily_file(client, mod_file)
        del_path = _download_daily_file(client, del_file)

    if mod_path is None and del_path is None:
        log.info("No daily files available — skipping ingestion")
        return

    # Ingest daily files into their staging tables via COPY
    if mod_path is not None:
        log.info(f"Ingesting {mod_file} into modifications table")
        try:
            _ingest_daily_file(mod_path, Modifications)
        except Exception:
            log.exception("Failed to ingest modifications file")
            return

    if del_path is not None:
        log.info(f"Ingesting {del_file} into deletions table")
        try:
            _ingest_deletions_file(del_path, yesterday)
        except Exception:
            log.exception("Failed to ingest deletions file")
            return

    # --- Step 2: Apply staged data to each out-of-sync dataset ----------
    conn_params = _get_conn_params()
    datasets_to_update = [
        ds for ds in all_datasets if ds.name in out_of_sync_names
    ]

    for ds in datasets_to_update:
        table_name = ds.model.__table__.name
        threshold = POPULATION_THRESHOLDS.get(ds.name, 0)

        if mod_path is not None:
            try:
                count = _upsert_modifications(
                    conn_params, table_name, yesterday, threshold
                )
                log.info(f"[{ds.name}] Upserted {count} modification(s)")
            except Exception:
                log.exception(f"[{ds.name}] Failed to apply modifications")

        if del_path is not None:
            try:
                count = _delete_from_dataset(conn_params, table_name, yesterday)
                log.info(f"[{ds.name}] Deleted {count} row(s)")
            except Exception:
                log.exception(f"[{ds.name}] Failed to apply deletions")

    # --- Step 3: Sync metadata (clears out_of_sync) ---------------------
    _sync_metadata(datasets_to_update)

    log.info(f"Daily update for {yesterday} complete")
