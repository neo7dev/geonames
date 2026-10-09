"""Daily update pipeline for GeoNames datasets.

Each dataset tracks ``last_applied_date``: the last day whose
``modifications-`` / ``deletes-`` files were applied to it. A run replays every
day after that up to yesterday (UTC), in order, so missed runs are caught up.
Applying a day is idempotent (upsert / delete by key), so replays are safe.

1. HEAD-check each dataset's remote file; out-of-sync enabled datasets are updated.
2. For each pending day: download the daily files, COPY them into the
   ``modifications`` / ``deletions`` staging tables, then apply them to every
   dataset still pending that day (population-filtered for city datasets).
   ``last_applied_date`` advances only after a day is applied to a dataset.
3. Datasets caught up to yesterday get their local etag / last-modified synced
   with the remote ones, which clears ``out_of_sync``. Failed or lagging
   datasets stay out of sync and are retried on the next run. Datasets never
   built or more than ``MAX_CATCHUP_DAYS`` behind are marked for a full rebuild
   instead.
"""

from __future__ import annotations

import io
from collections.abc import Callable
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
from geonames_server.ingestion.pipeline import (
    _get_conn_params,
    check_remote_metadata,
    dataset_lock,
)
from geonames_server.ingestion.registry import (
    DatasetInfo,
    enabled_names,
    get_all_datasets,
)
from geonames_server.ingestion.remote import RemoteFileClient
from geonames_server.ingestion.sync_tracker import DatasetSyncStatus, SyncStep
from geonames_server.log.mylogger import get_logger
from geonames_server.utils.utils import current_utc_date

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

# Upper bound on how many days a single run will replay.
MAX_CATCHUP_DAYS = 31


class DailyUpdateError(Exception):
    """A day's files could not be downloaded or staged."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _yesterday() -> date:
    return current_utc_date() - timedelta(days=1)


def _pending_days(last_applied: date | None, yesterday: date) -> list[date] | None:
    """Days after *last_applied* up to *yesterday*, oldest first.

    Returns ``None`` if *last_applied* is unknown (the dataset was never built)
    or the gap exceeds ``MAX_CATCHUP_DAYS``: daily files cannot close it, so the
    dataset needs a full rebuild.
    """
    if last_applied is None:
        return None
    gap = (yesterday - last_applied).days
    if gap > MAX_CATCHUP_DAYS:
        return None
    return [last_applied + timedelta(days=i) for i in range(1, gap + 1)]


def _download_daily_file(client: RemoteFileClient, file_name: str) -> Path | None:
    """Download a daily file if it exists on the remote server.

    Returns the local path, or ``None`` if the file does not exist. Download
    failures propagate.
    """
    url = settings.geonames_url + file_name

    if not client.exists(url):
        log.info(f"Daily file not available: {url}")
        return None

    with Downloader(
        cache_dir=settings.cache_dir, data_dir=settings.data_dir, force=True
    ) as dl:
        return dl.download(url=url, file_name=file_name).path


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
# Catch-up bookkeeping
# ---------------------------------------------------------------------------


def _load_last_applied(names: list[str]) -> dict[str, date | None]:
    with get_session() as session:
        repo = DatasetMetaDataRepository(session)
        return {
            n: (r.last_applied_date if (r := repo.get_by_dataset(n)) else None)
            for n in names
        }


def _set_last_applied(name: str, day: date) -> None:
    with get_session() as session:
        record = DatasetMetaDataRepository(session).get_by_dataset(name)
        if record is not None:
            record.last_applied_date = day


def _require_rebuild(name: str) -> None:
    """Clear ``last_modified`` so the metadata listener sets ``needs_rebuild``."""
    with get_session() as session:
        record = DatasetMetaDataRepository(session).get_by_dataset(name)
        if record is not None:
            record.last_modified = None


def _stage_day(
    day: date, on_step: Callable[[SyncStep], None], allow_partial: bool = False
) -> bool:
    """Download and stage *day*'s files.

    Returns False (nothing staged) if the day is not published: neither file
    exists, or only one does and *allow_partial* is False.
    """
    mod_file = f"modifications-{day.isoformat()}.txt"
    del_file = f"deletes-{day.isoformat()}.txt"

    try:
        with RemoteFileClient() as client:
            on_step(SyncStep.DOWNLOADING_MODIFICATIONS)
            mod_path = _download_daily_file(client, mod_file)
            on_step(SyncStep.DOWNLOADING_DELETES)
            del_path = _download_daily_file(client, del_file)

        if mod_path is None and del_path is None:
            return False
        if mod_path is None or del_path is None:
            if not allow_partial:
                return False
            log.warning(f"Only one daily file published for {day}; applying it alone")

        if mod_path is not None:
            on_step(SyncStep.INGESTING_MODIFICATIONS)
            _ingest_daily_file(mod_path, Modifications)
        if del_path is not None:
            on_step(SyncStep.INGESTING_DELETES)
            _ingest_deletions_file(del_path, day)
    except Exception as e:
        raise DailyUpdateError(f"Failed to stage daily files for {day}: {e}") from e

    return True


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def apply_daily_updates(
    datasets: list[DatasetInfo],
    out_of_sync_names: set[str],
    on_step: Callable[[SyncStep], None] = lambda _s: None,
    on_dataset: Callable[[str, DatasetSyncStatus], None] = lambda _n, _s: None,
) -> None:
    """Replay pending daily files for the out-of-sync, enabled datasets.

    Disabled datasets are never touched. Raises :class:`DailyUpdateError` if a day's files cannot be staged.
    """
    yesterday = _yesterday()
    enabled = enabled_names(settings.enabled_datasets)
    targets = [
        ds for ds in datasets if ds.name in out_of_sync_names and ds.name in enabled
    ]
    pending: dict[str, set[date]] = {}
    for name, last in _load_last_applied([ds.name for ds in targets]).items():
        days = _pending_days(last, yesterday)
        if days is None:
            reason = (
                "never built"
                if last is None
                else f"last applied {last}, over {MAX_CATCHUP_DAYS} days ago"
            )
            log.warning(f"[{name}] {reason} — marking for a full rebuild")
            _require_rebuild(name)
            on_dataset(name, DatasetSyncStatus.FAILED)
            continue
        pending[name] = set(days)
    targets = [ds for ds in targets if ds.name in pending]
    caught_up = {n for n, days in pending.items() if not days}
    failed: set[str] = set()
    conn_params = _get_conn_params()

    for day in sorted(set().union(*pending.values())):
        todo = [ds for ds in targets if ds.name not in failed and day in pending[ds.name]]
        if not todo:
            continue

        log.info(f"Applying daily files for {day} to {[d.name for d in todo]}")
        # Yesterday may still be mid-upload, so it needs both files; an older
        # day missing one will not get it later.
        published = _stage_day(day, on_step, allow_partial=day < yesterday)
        if not published:
            if day >= yesterday:
                log.info(f"Daily files for {day} not published yet — will retry")
                break
            log.warning(f"No daily files for {day}; skipping that day")

        on_step(SyncStep.APPLYING_DATASETS)
        for ds in todo:
            table = ds.model.__table__.name
            try:
                # Blocks while a rebuild of this dataset is loading / swapping.
                with dataset_lock(ds.name):
                    if published:
                        on_dataset(ds.name, DatasetSyncStatus.APPLYING_MODIFICATIONS)
                        count = _upsert_modifications(
                            conn_params, table, day, POPULATION_THRESHOLDS.get(ds.name, 0)
                        )
                        log.info(f"[{ds.name}] {day}: upserted {count} modification(s)")
                        on_dataset(ds.name, DatasetSyncStatus.APPLYING_DELETES)
                        count = _delete_from_dataset(conn_params, table, day)
                        log.info(f"[{ds.name}] {day}: deleted {count} row(s)")
                    _set_last_applied(ds.name, day)
            except Exception:
                log.exception(f"[{ds.name}] Failed to apply {day}")
                failed.add(ds.name)
                on_dataset(ds.name, DatasetSyncStatus.FAILED)
                continue
            if day == yesterday:
                caught_up.add(ds.name)

    synced = [ds for ds in targets if ds.name in caught_up and ds.name not in failed]
    on_step(SyncStep.SYNCING_METADATA)
    _sync_metadata(synced)
    for ds in targets:
        if ds.name in failed:
            continue
        status = (
            DatasetSyncStatus.SYNCED
            if ds.name in caught_up
            else DatasetSyncStatus.WAITING
        )
        on_dataset(ds.name, status)


def check_and_apply_daily_updates() -> None:
    """Scheduler entry point: HEAD-check remotes, then catch up out-of-sync datasets."""
    all_datasets = get_all_datasets()
    log.info(f"Starting daily update check for {_yesterday()}")

    check_remote_metadata(all_datasets)

    with get_session() as session:
        out_of_sync_names = {
            r.dataset for r in DatasetMetaDataRepository(session).get_out_of_sync()
        } & enabled_names(settings.enabled_datasets)

    if not out_of_sync_names:
        log.info("All datasets are in sync — nothing to do")
        return

    log.info(f"Out-of-sync datasets: {sorted(out_of_sync_names)}")
    try:
        apply_daily_updates(all_datasets, out_of_sync_names)
    except DailyUpdateError:
        log.exception("Daily update aborted")
        return

    log.info("Daily update complete")
