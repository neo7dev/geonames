from pathlib import Path
from typing import Type

import psycopg2
from psycopg2 import sql
from sqlalchemy.orm import DeclarativeMeta

from geonames_server.config.config import settings
from geonames_server.rich_console.ingestion_progressbar import (
    IngestionProgressBar,
    _ProgressFileWrapper,
)


class Ingestor:
    def __init__(self, conn_info: dict | None = None) -> None:
        self.conn_params = conn_info or {
            "user": settings.postgres_user,
            "password": settings.postgres_password,
            "host": settings.postgres_host,
            "port": settings.postgres_port,
            "dbname": settings.postgres_db,
            "options": settings.postgres_options,
        }

    @staticmethod
    def _get_conflict_columns(column_names: list[str], table) -> list[str]:
        """Resolve conflict keys from primary key columns, with safe fallback."""
        primary_key = getattr(table, "primary_key", None)
        pk_columns = getattr(primary_key, "columns", None)
        if pk_columns is not None:
            resolved = [c.name for c in pk_columns]
            if resolved:
                return resolved

        if "geonameid" in column_names:
            return ["geonameid"]
        return [column_names[0]]

    @staticmethod
    def _copy_to_temp(
        cur, target_table: str, temp_table: str, columns: list[str], source
    ) -> None:
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

    @staticmethod
    def _merge_unique_rows(
        cur,
        temp_table: str,
        target_table: str,
        columns: list[str],
        conflict_columns: list[str],
        update_columns: list[str],
    ) -> None:
        if update_columns:
            conflict_action = sql.SQL("DO UPDATE SET {updates}").format(
                updates=sql.SQL(", ").join(
                    sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(c))
                    for c in update_columns
                )
            )
        else:
            conflict_action = sql.SQL("DO NOTHING")

        merge_sql = sql.SQL(
            "WITH staged AS ("
            "  SELECT {columns}, "
            "         COUNT(*) OVER (PARTITION BY {conflict_columns}) AS duplicate_count "
            "  FROM {temp}"
            ") "
            "INSERT INTO {target} ({columns}) "
            "SELECT {columns} FROM staged WHERE duplicate_count = 1 "
            "ON CONFLICT ({conflict_columns}) "
            "{conflict_action}"
        ).format(
            temp=sql.Identifier(temp_table),
            target=sql.Identifier(target_table),
            columns=sql.SQL(", ").join(map(sql.Identifier, columns)),
            conflict_columns=sql.SQL(", ").join(map(sql.Identifier, conflict_columns)),
            conflict_action=conflict_action,
        )
        cur.execute(merge_sql)

    def ingest(self, file_path: str | Path, model: Type[DeclarativeMeta]) -> None:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Input file not found: {file_path}")

        total_bytes = path.stat().st_size

        table = model.__table__
        column_names = [c.name for c in table.columns]
        conflict_columns = self._get_conflict_columns(column_names, table)
        update_columns = [c for c in column_names if c not in set(conflict_columns)]
        temp_table = f"tmp_{table.name}_ingest_stage"

        with IngestionProgressBar(total_bytes, f"Ingesting {path.name}") as pb:
            wrapper = _ProgressFileWrapper(path, pb)

            try:
                with psycopg2.connect(**self.conn_params) as conn:
                    with conn.cursor() as cur:
                        self._copy_to_temp(
                            cur,
                            target_table=table.name,
                            temp_table=temp_table,
                            columns=column_names,
                            source=wrapper,
                        )
                        self._merge_unique_rows(
                            cur,
                            temp_table=temp_table,
                            target_table=table.name,
                            columns=column_names,
                            conflict_columns=conflict_columns,
                            update_columns=update_columns,
                        )
            finally:
                wrapper.close()
