"""Build the dataset tables' missing indexes without holding up startup.

Migrations run before the API listens, and a trigram index on allcountries
(13M rows) takes minutes to build, so the migrations that add indexes build
them only on empty tables. The dataset startup thread then calls
build_missing_indexes(), which builds the rest with CREATE INDEX
CONCURRENTLY: reads and writes go on meanwhile, the filters are just slow
until it finishes.
"""

from __future__ import annotations

import psycopg2
from psycopg2 import sql
from sqlalchemy import Index
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from geonames_server.ingestion.pipeline import (
    INDEX_BUILD_MEMORY,
    _get_conn_params,
    dataset_lock,
)
from geonames_server.ingestion.registry import DatasetInfo
from geonames_server.log.mylogger import get_logger

log = get_logger()

# 'missing', 'invalid' (a concurrent build that failed or was interrupted) or 'valid'
_STATE = (
    "SELECT CASE WHEN i.indexrelid IS NULL THEN 'missing' "
    "WHEN i.indisvalid THEN 'valid' ELSE 'invalid' END "
    "FROM (SELECT to_regclass(%s) AS oid) r "
    "LEFT JOIN pg_index i ON i.indexrelid = r.oid"
)


def _concurrent_ddl(index: Index) -> str:
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    return ddl.replace("CREATE INDEX ", "CREATE INDEX CONCURRENTLY ", 1)


def build_missing_indexes(datasets: list[DatasetInfo]) -> list[str]:
    """Build each dataset table's model indexes that are missing or invalid.

    Each build holds the dataset's lock, so a rebuild cannot swap the table out
    from under it. A failed build is logged and does not stop the others.
    Returns the names of the indexes that failed.
    """
    failed = []
    conn = psycopg2.connect(**_get_conn_params())
    # CREATE INDEX CONCURRENTLY cannot run in a transaction.
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET maintenance_work_mem = '{INDEX_BUILD_MEMORY}'")
        for ds in datasets:
            for index in sorted(ds.model.__table__.indexes, key=lambda i: i.name):
                with conn.cursor() as cur:
                    cur.execute(_STATE, (index.name,))
                    if cur.fetchone()[0] == "valid":
                        continue
                log.info(f"[{ds.name}] Building index {index.name}")
                try:
                    with dataset_lock(ds.name), conn.cursor() as cur:
                        cur.execute(_STATE, (index.name,))
                        state = cur.fetchone()[0]
                        if state == "invalid":
                            cur.execute(
                                sql.SQL("DROP INDEX CONCURRENTLY {}").format(
                                    sql.Identifier(index.name)
                                )
                            )
                        if state != "valid":
                            cur.execute(_concurrent_ddl(index))
                    log.info(f"[{ds.name}] Built index {index.name}")
                except Exception:
                    log.exception(f"[{ds.name}] Could not build index {index.name}")
                    failed.append(index.name)
    finally:
        conn.close()
    return failed
