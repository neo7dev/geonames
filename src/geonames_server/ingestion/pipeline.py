from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from psycopg2 import sql

from geonames_server.config.config import settings
from geonames_server.database.models.dataset_metadata import DatasetMetaData
from geonames_server.database.repository.sync.dataset_metadata_repository import (
    DatasetMetaDataRepository,
)
from geonames_server.database.session import get_session
from geonames_server.ingestion.downloader import Downloader
from geonames_server.ingestion.extractor import Extractor
from geonames_server.ingestion.registry import DatasetInfo, get_enabled_datasets
from geonames_server.ingestion.remote import RemoteFileClient
from geonames_server.log.mylogger import get_logger

log = get_logger()


def _get_conn_params() -> dict:
    return {
        "user": settings.postgres_user,
        "password": settings.postgres_password,
        "host": settings.postgres_host,
        "port": settings.postgres_port,
        "dbname": settings.postgres_db,
    }


def truncate_table(table_name: str) -> None:
    """TRUNCATE a table using psycopg2."""
    truncate_sql = sql.SQL("TRUNCATE TABLE {table} CASCADE").format(
        table=sql.Identifier(table_name),
    )
    with psycopg2.connect(**_get_conn_params()) as conn:
        with conn.cursor() as cur:
            cur.execute(truncate_sql.as_string(conn))


def seed_dataset_metadata(datasets: list[DatasetInfo]) -> None:
    """Ensure a DatasetMetaData row exists for every dataset.

    Creates rows with out_of_sync=True and needs_rebuild=True for any
    dataset that doesn't already have a metadata entry.
    Should be called with *all* datasets (not just enabled ones) so that
    every table has a metadata record.
    """
    with get_session() as session:
        repo = DatasetMetaDataRepository(session)
        for ds in datasets:
            existing = repo.get_by_dataset(ds.name)
            if existing is None:
                metadata = DatasetMetaData(
                    dataset=ds.name,
                    zip_file=ds.zip_file,
                    txt_file=ds.txt_file,
                    out_of_sync=True,
                    needs_rebuild=True,
                )
                repo.add(metadata)
                log.debug(f"Created metadata entry for dataset: {ds}")
                log.info(f"Seeded metadata for dataset: {ds.name}")


def check_remote_metadata(datasets: list[DatasetInfo]) -> None:
    """HEAD-request every dataset's remote file and update metadata.

    For each dataset, sends a HEAD request to the zip URL (if present) or
    the txt URL.  On a 2XX response the dataset's ``remote_etag`` and
    ``remote_last_modified`` are updated.  The SQLAlchemy event listeners on
    :class:`DatasetMetaData` then recompute ``out_of_sync`` and
    ``needs_rebuild`` automatically.
    """
    with RemoteFileClient() as client, get_session() as session:
        repo = DatasetMetaDataRepository(session)
        for ds in datasets:
            file_name = ds.zip_file if ds.zip_file else ds.txt_file
            url = settings.geonames_url + file_name
            try:
                meta = client.head(url)
            except Exception:
                log.warning(
                    f"[{ds.name}] HEAD request failed for {url} — skipping remote metadata update"
                )
                continue

            record = repo.get_by_dataset(ds.name)
            if record is None:
                log.warning(f"[{ds.name}] No metadata row — skipping")
                continue

            log.debug(
                f"[{record.dataset}] has local etag={record.etag}, remote_etag={record.remote_etag}, local_last_modified={record.last_modified}, remote_last_modified={record.remote_last_modified}"
            )

            record.remote_etag = meta.etag
            record.remote_last_modified = meta.last_modified
            session.commit()

            log.info(
                f"[{ds.name}] Remote metadata updated — "
                f"etag={meta.etag}, last_modified={meta.last_modified}, "
                f"out_of_sync={record.out_of_sync}, needs_rebuild={record.needs_rebuild}"
            )


def build_dataset(dataset_info: DatasetInfo, force: bool = False) -> None:
    """Builds a single dataset.

    Full download -> extract -> truncate -> ingest pipeline for one dataset.
    TRUNCATE + COPY run in a single psycopg2 transaction for atomicity.
    """
    name = dataset_info.name
    zip_file = dataset_info.zip_file
    txt_file = dataset_info.txt_file
    file_to_download = zip_file if zip_file is not None and zip_file != "" else txt_file
    model = dataset_info.model
    table_name = model.__table__.name

    url = settings.geonames_url + file_to_download

    log.info(f"[{name}] Starting dataset sync (force={force})")

    # --- Download ---
    try:
        with Downloader(
            cache_dir=settings.cache_dir, data_dir=settings.data_dir, force=force
        ) as dl:
            result = dl.download(url=url, file_name=file_to_download)
    except Exception:
        log.exception(f"[{name}] Download failed")
        raise

    # --- Extract ---
    if zip_file is not None and zip_file != "":
        zip_path = settings.data_dir / zip_file
        try:
            Extractor(settings.data_dir).extract(zip_path)
        except Exception:
            log.exception(f"[{name}] Extraction failed")
            raise

    # --- Truncate + Ingest (single transaction) ---
    txt_path = settings.data_dir / txt_file
    if not txt_path.exists():
        log.error(f"[{name}] Extracted file not found: {txt_path}")
        raise FileNotFoundError(f"Extracted file not found: {txt_path}")

    try:
        _truncate_and_ingest(model, table_name, txt_path)
    except Exception:
        log.exception(f"[{name}] Ingestion failed")
        raise

    # --- Mark synced ---
    now = datetime.now(timezone.utc)

    with get_session() as session:
        repo = DatasetMetaDataRepository(session)
        metadata = repo.get_by_dataset(name)
        if metadata:
            # Capture remote metadata from the download response headers
            metadata.remote_etag = result.etag
            metadata.remote_last_modified = result.last_modified
            # After a fresh build, local etag matches remote
            metadata.etag = result.etag
            metadata.last_modified = now
            session.commit()

    log.info(f"[{name}] Dataset sync complete")


def _truncate_and_ingest(model, table_name: str, txt_path: Path) -> None:
    """TRUNCATE + COPY in a single transaction for atomicity.

    Readers see old data until the new load commits.
    """
    conn_params = _get_conn_params()
    column_names = [c.name for c in model.__table__.columns]
    conflict_columns = ["geonameid"] if "geonameid" in column_names else [column_names[0]]
    update_columns = [c for c in column_names if c not in set(conflict_columns)]
    temp_table = f"tmp_{table_name}_rebuild_stage"

    truncate_sql = sql.SQL("TRUNCATE TABLE {table} CASCADE").format(
        table=sql.Identifier(table_name),
    )

    create_temp_sql = sql.SQL(
        "CREATE TEMP TABLE {temp} (LIKE {target} INCLUDING DEFAULTS) ON COMMIT DROP"
    ).format(
        temp=sql.Identifier(temp_table),
        target=sql.Identifier(table_name),
    )

    quoted_columns = ", ".join(f'"{col}"' for col in column_names)
    copy_sql = (
        f'COPY "{temp_table}" ({quoted_columns}) '
        "FROM STDIN "
        "WITH (FORMAT csv, DELIMITER E'\\t', NULL '', QUOTE E'\\b', ESCAPE E'\\b')"
    )

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
        target=sql.Identifier(table_name),
        columns=sql.SQL(", ").join(map(sql.Identifier, column_names)),
        conflict_columns=sql.SQL(", ").join(map(sql.Identifier, conflict_columns)),
        updates=sql.SQL(", ").join(
            sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(c))
            for c in update_columns
        ),
    )

    from geonames_server.rich_console.ingestion_progressbar import (
        IngestionProgressBar,
        _ProgressFileWrapper,
    )

    total_bytes = txt_path.stat().st_size

    with IngestionProgressBar(total_bytes, f"Ingesting {txt_path.name}") as pb:
        wrapper = _ProgressFileWrapper(txt_path, pb)
        try:
            with psycopg2.connect(**conn_params) as conn:
                conn.autocommit = False
                with conn.cursor() as cur:
                    cur.execute(truncate_sql)
                    cur.execute(create_temp_sql)
                    cur.copy_expert(copy_sql, wrapper)
                    cur.execute(merge_sql)
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            wrapper.close()


def build_all_datasets() -> None:
    """Rebuild enabled datasets that have needs_rebuild=True."""
    enabled = get_enabled_datasets(settings.enabled_datasets)

    # Query which enabled datasets actually need a rebuild
    with get_session() as session:
        repo = DatasetMetaDataRepository(session)
        to_build = []
        for ds in enabled:
            metadata = repo.get_by_dataset(ds.name)
            if metadata is None or metadata.needs_rebuild:
                to_build.append(ds)

    if not to_build:
        log.info("No enabled datasets need rebuilding")
        return

    log.info(f"Rebuilding {len(to_build)} dataset(s): {[d.name for d in to_build]}")

    failed = []
    for ds in to_build:
        try:
            build_dataset(ds, force=True)
        except Exception:
            log.exception(f"Failed to build dataset: {ds.name}")
            failed.append(ds.name)

    if failed:
        log.warning(f"Build completed with failures: {failed}")
    else:
        log.info("All datasets built successfully")
