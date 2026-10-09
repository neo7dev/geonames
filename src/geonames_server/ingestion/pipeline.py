from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
        "options": settings.postgres_options,
    }


# First key of the advisory locks taken by dataset_lock (arbitrary, app-wide).
_DATASET_LOCK_NAMESPACE = 0x6E6D  # "gn"


@contextmanager
def dataset_lock(name: str) -> Iterator[None]:
    """Hold a Postgres advisory lock that serialises writers of dataset *name*.

    Rebuilds and daily updates both take it, so a daily update cannot write to a
    table that a rebuild is about to swap out, and their ``last_applied_date``
    writes cannot interleave. Works across processes.
    """
    key = (_DATASET_LOCK_NAMESPACE, name)
    conn = psycopg2.connect(**_get_conn_params())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s, hashtext(%s))", key)
        try:
            yield
        finally:
            # Unlock explicitly: the server releases it on disconnect only
            # asynchronously.
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s, hashtext(%s))", key)
    finally:
        conn.close()


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

    Full download -> extract -> load into a staging table -> atomic swap.
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

    # --- Load + swap (single transaction) ---
    txt_path = settings.data_dir / txt_file
    if not txt_path.exists():
        log.error(f"[{name}] Extracted file not found: {txt_path}")
        raise FileNotFoundError(f"Extracted file not found: {txt_path}")

    # Daily updates wait until the swap and metadata write are both done.
    with dataset_lock(name):
        try:
            _load_and_swap(model, table_name, txt_path)
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
                # The dump contains every change up to the day before it was built.
                built = result.last_modified or now
                metadata.last_applied_date = built.date() - timedelta(days=1)
                session.commit()

    log.info(f"[{name}] Dataset sync complete")


class RowCountError(Exception):
    """The freshly loaded table is empty or implausibly smaller than the live one."""


# Refuse to swap in a load with fewer than this fraction of the live row count.
MIN_ROW_RATIO = 0.9
# Fail the swap instead of queueing behind long-running readers.
SWAP_LOCK_TIMEOUT = "30s"

_INDEXDEF = re.compile(r"^(CREATE (?:UNIQUE )?INDEX )\S+ ON \S+ (.*)$", re.DOTALL)


def _live_rows(cur, table: str) -> int:
    """Planner row estimate for *table*, avoiding a full scan.

    Falls back to an exact count if the table was never analysed.
    """
    cur.execute(
        "SELECT reltuples::bigint FROM pg_class WHERE oid = to_regclass(%s)", (table,)
    )
    estimate = cur.fetchone()[0]
    if estimate >= 0:
        return estimate
    cur.execute(sql.SQL("SELECT COUNT(*) FROM {t}").format(t=sql.Identifier(table)))
    return cur.fetchone()[0]


def _clone_indexes(
    cur, table: str, staged: str, pk_name: str | None
) -> list[tuple[str, str]]:
    """Recreate *table*'s secondary indexes on *staged* under temporary names.

    Returns ``(temporary_name, final_name)`` pairs to rename after the swap.
    """
    cur.execute(
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = current_schema() AND tablename = %s",
        (table,),
    )
    renames = []
    for name, definition in cur.fetchall():
        if name == pk_name:
            continue
        m = _INDEXDEF.match(definition)
        if m is None:
            raise RuntimeError(f"Cannot parse index definition: {definition}")
        tmp = f"{name}_new"
        cur.execute(
            sql.SQL(m.group(1))
            + sql.Identifier(tmp)
            + sql.SQL(" ON ")
            + sql.Identifier(staged)
            + sql.SQL(" " + m.group(2))
        )
        renames.append((tmp, name))
    return renames


def _transfer_sequences(cur, old: str, table: str) -> None:
    """Re-own sequences of *old*'s columns to *table* so dropping *old* keeps them.

    The staged table copies column defaults (``nextval(...)``), which point at the
    old table's sequences.
    """
    cur.execute(
        "SELECT s.relname, a.attname FROM pg_class s "
        "JOIN pg_depend d ON d.objid = s.oid AND d.deptype = 'a' "
        "JOIN pg_attribute a ON a.attrelid = d.refobjid AND a.attnum = d.refobjsubid "
        "WHERE s.relkind = 'S' AND d.refobjid = to_regclass(%s)",
        (old,),
    )
    for seq, column in cur.fetchall():
        cur.execute(
            sql.SQL("ALTER SEQUENCE {s} OWNED BY {t}.{c}").format(
                s=sql.Identifier(seq),
                t=sql.Identifier(table),
                c=sql.Identifier(column),
            )
        )


def _load_and_swap(model, table_name: str, txt_path: Path) -> None:
    """Load *txt_path* into a staging table, then swap it in atomically.

    Everything runs in one transaction. The live table keeps serving reads
    during the load; only the final renames take a brief exclusive lock. The
    swap is refused if the new table is empty or much smaller than the live one.
    """
    column_names = [c.name for c in model.__table__.columns]
    key = ["geonameid"] if "geonameid" in column_names else [column_names[0]]
    staged = f"{table_name}_new"
    old = f"{table_name}_old"

    ident = sql.Identifier
    keys = sql.SQL(", ").join(map(ident, key))
    quoted_columns = ", ".join(f'"{col}"' for col in column_names)
    copy_sql = (
        f'COPY "{staged}" ({quoted_columns}) '
        "FROM STDIN "
        "WITH (FORMAT csv, DELIMITER E'\\t', NULL '', QUOTE E'\\b', ESCAPE E'\\b')"
    )

    from geonames_server.rich_console.ingestion_progressbar import (
        IngestionProgressBar,
        _ProgressFileWrapper,
    )

    total_bytes = txt_path.stat().st_size

    with IngestionProgressBar(total_bytes, f"Ingesting {txt_path.name}") as pb:
        wrapper = _ProgressFileWrapper(txt_path, pb)
        conn = None
        try:
            conn = psycopg2.connect(**_get_conn_params())
            conn.autocommit = False
            with conn.cursor() as cur:
                # No indexes yet: bulk load is faster, indexes are built after.
                cur.execute(
                    sql.SQL("CREATE TABLE {s} (LIKE {t} INCLUDING DEFAULTS)").format(
                        s=ident(staged), t=ident(table_name)
                    )
                )
                cur.copy_expert(copy_sql, wrapper)
                copied = cur.rowcount

                # Keys that appear more than once are dropped entirely.
                cur.execute(
                    sql.SQL(
                        "DELETE FROM {s} WHERE ({k}) IN "
                        "(SELECT {k} FROM {s} GROUP BY {k} HAVING COUNT(*) > 1)"
                    ).format(s=ident(staged), k=keys)
                )

                # Checked after deduplication: that is what would be swapped in.
                new_rows = copied - cur.rowcount
                live_rows = _live_rows(cur, table_name)
                if new_rows == 0 or new_rows < live_rows * MIN_ROW_RATIO:
                    raise RowCountError(
                        f"{table_name}: loaded {new_rows} rows vs {live_rows} live; "
                        "refusing to swap"
                    )

                cur.execute(
                    sql.SQL("ALTER TABLE {s} ADD PRIMARY KEY ({k})").format(
                        s=ident(staged), k=keys
                    )
                )

                cur.execute(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = to_regclass(%s) AND contype = 'p'",
                    (table_name,),
                )
                row = cur.fetchone()
                pk_name = row[0] if row else None
                index_renames = _clone_indexes(cur, table_name, staged, pk_name)
                # Fresh planner statistics, so queries plan well right after the swap.
                cur.execute(sql.SQL("ANALYZE {s}").format(s=ident(staged)))

                cur.execute(f"SET LOCAL lock_timeout = '{SWAP_LOCK_TIMEOUT}'")
                cur.execute(
                    sql.SQL("ALTER TABLE {t} RENAME TO {o}").format(
                        t=ident(table_name), o=ident(old)
                    )
                )
                cur.execute(
                    sql.SQL("ALTER TABLE {s} RENAME TO {t}").format(
                        s=ident(staged), t=ident(table_name)
                    )
                )
                _transfer_sequences(cur, old, table_name)
                cur.execute(sql.SQL("DROP TABLE {o}").format(o=ident(old)))
                if pk_name:
                    cur.execute(
                        sql.SQL("ALTER TABLE {t} RENAME CONSTRAINT {s} TO {p}").format(
                            t=ident(table_name),
                            s=ident(f"{staged}_pkey"),
                            p=ident(pk_name),
                        )
                    )
                for tmp, final in index_renames:
                    cur.execute(
                        sql.SQL("ALTER INDEX {a} RENAME TO {b}").format(
                            a=ident(tmp), b=ident(final)
                        )
                    )
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            wrapper.close()
            if conn is not None:
                conn.close()


def build_all_datasets() -> list[str]:
    """Rebuild enabled datasets that have needs_rebuild=True.

    A failed build is logged and does not stop the others. Returns the names
    of the datasets that failed to build.
    """
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
        return []

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
    return failed
