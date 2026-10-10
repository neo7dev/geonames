"""Postgres extensions the geonames tables need.

pg_trgm backs the trigram indexes that make the substring name filters
(ILIKE '%...%') fast. It lives in its own schema, as Supabase installs
extensions, so dropping the geonames schema for a rebuild leaves it in place.
"""

from sqlalchemy import Connection, text

TRGM_SCHEMA = "extensions"
GIN_TRGM_OPS = f"{TRGM_SCHEMA}.gin_trgm_ops"


def install_pg_trgm(conn: Connection) -> None:
    """Install pg_trgm in TRGM_SCHEMA, unless it is already there."""
    if conn.scalar(text("SELECT to_regnamespace(:s)"), {"s": TRGM_SCHEMA}) is None:
        conn.execute(text(f"CREATE SCHEMA {TRGM_SCHEMA}"))
    conn.execute(
        text(f"CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA {TRGM_SCHEMA}")
    )
    schema = conn.scalar(
        text(
            "SELECT extnamespace::regnamespace::text FROM pg_extension "
            "WHERE extname = 'pg_trgm'"
        )
    )
    if schema != TRGM_SCHEMA:
        raise RuntimeError(
            f"pg_trgm is installed in schema {schema!r}, the indexes expect "
            f"{TRGM_SCHEMA!r}: run ALTER EXTENSION pg_trgm SET SCHEMA {TRGM_SCHEMA}"
        )
