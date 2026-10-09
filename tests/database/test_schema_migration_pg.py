"""Integration tests for running migrations into the app's own schema.

Needs TEST_PG_DSN pointing at a throwaway database: each test drops the app's
schema and the app's tables in public.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import psycopg2
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg2 import sql
from psycopg2.extensions import parse_dsn
from sqlalchemy import URL, create_engine

import geonames_server.database.models  # noqa: F401 — register every model
from geonames_server.config.config import settings
from geonames_server.database.models.base import Base

DSN = os.environ.get("TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_PG_DSN not set")

SCHEMA = settings.postgres_schema
APP_TABLES = {*Base.metadata.tables, "alembic_version"}
API_ROLES = ("anon", "authenticated", "service_role")
# The last revision before the grants were revoked, i.e. what older versions left behind.
LEGACY_REVISION = "97d04a096bc4"


def _alembic_config() -> Config:
    params = parse_dsn(DSN)
    url = URL.create(
        "postgresql+psycopg",
        username=params.get("user"),
        password=params.get("password"),
        host=params.get("host"),
        port=int(params["port"]) if params.get("port") else None,
        database=params.get("dbname"),
    )
    cfg = Config(str(settings.alembic_ini))
    # ConfigParser treats % as interpolation
    cfg.set_main_option(
        "sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%")
    )
    return cfg


def _query(query: str, *args) -> list[tuple]:
    conn = psycopg2.connect(DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(query, args)
            return cur.fetchall()
    finally:
        conn.close()


def _tables(schema: str) -> set[str]:
    rows = _query("SELECT tablename FROM pg_tables WHERE schemaname = %s", schema)
    return {name for (name,) in rows}


def _api_role_grants(schema: str) -> list[tuple]:
    return _query(
        "SELECT grantee, table_name FROM information_schema.role_table_grants "
        "WHERE table_schema = %s AND grantee = ANY(%s)",
        schema,
        list(API_ROLES),
    )


def _reset() -> None:
    conn = psycopg2.connect(DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(SCHEMA)
                )
            )
            for name in APP_TABLES:
                cur.execute(
                    sql.SQL("DROP TABLE IF EXISTS public.{}").format(
                        sql.Identifier(name)
                    )
                )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def clean_db():
    _reset()
    yield
    _reset()


def _create_legacy_layout() -> None:
    """Recreate what older versions left behind: every table in public."""
    engine = create_engine(
        "postgresql+psycopg2://",
        creator=lambda: psycopg2.connect(DSN, options="-c search_path=public"),
    )
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()

    conn = psycopg2.connect(DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE public.alembic_version ("
                "version_num varchar(32) PRIMARY KEY)"
            )
            cur.execute(
                "INSERT INTO public.alembic_version VALUES (%s)", (LEGACY_REVISION,)
            )
            cur.execute(
                "INSERT INTO public.dataset_metadata (dataset, txt_file) "
                "VALUES ('cities500', 'cities500.txt')"
            )
            # What Supabase's default privileges grant on new public tables
            for role in API_ROLES:
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
                if cur.fetchone():
                    cur.execute(
                        sql.SQL(
                            "GRANT ALL ON ALL TABLES IN SCHEMA public TO {}"
                        ).format(sql.Identifier(role))
                    )
        conn.commit()
    finally:
        conn.close()


def _head(cfg: Config) -> str:
    return ScriptDirectory.from_config(cfg).get_current_head()


def test_fresh_database_creates_every_table_in_the_schema():
    cfg = _alembic_config()

    command.upgrade(cfg, "head")

    assert APP_TABLES <= _tables(SCHEMA)
    assert not APP_TABLES & _tables("public")
    assert _query(f'SELECT version_num FROM "{SCHEMA}".alembic_version') == [
        (_head(cfg),)
    ]
    assert _api_role_grants(SCHEMA) == []


def test_legacy_public_tables_move_with_data_and_history():
    _create_legacy_layout()
    cfg = _alembic_config()

    command.upgrade(cfg, "head")

    assert APP_TABLES <= _tables(SCHEMA)
    assert not APP_TABLES & _tables("public")
    assert _query(f'SELECT dataset FROM "{SCHEMA}".dataset_metadata') == [
        ("cities500",)
    ]
    assert _query(f'SELECT version_num FROM "{SCHEMA}".alembic_version') == [
        (_head(cfg),)
    ]
    assert _api_role_grants(SCHEMA) == []


def test_upgrade_is_idempotent():
    cfg = _alembic_config()

    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")

    assert APP_TABLES <= _tables(SCHEMA)
    assert not APP_TABLES & _tables("public")


def test_app_connections_resolve_tables_in_the_schema():
    command.upgrade(_alembic_config(), "head")

    conn = psycopg2.connect(DSN, options=settings.postgres_options)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT current_schema(), to_regclass('dataset_metadata')::text"
            )
            current, table = cur.fetchone()
    finally:
        conn.close()

    assert current == SCHEMA
    assert table == "dataset_metadata"


def test_force_rebuild_drops_schema_and_legacy_public_tables():
    from geonames_server.database import migrations

    command.upgrade(_alembic_config(), "head")
    _create_legacy_layout()
    engine = create_engine(
        "postgresql+psycopg2://",
        creator=lambda: psycopg2.connect(DSN, options=settings.postgres_options),
    )

    try:
        with patch.object(migrations, "sync_engine", engine):
            migrations._drop_all_tables()
    finally:
        engine.dispose()

    assert _query("SELECT 1 FROM pg_namespace WHERE nspname = %s", SCHEMA) == []
    assert not APP_TABLES & _tables("public")


def _drop_with_app_engine() -> None:
    from geonames_server.database import migrations

    engine = create_engine(
        "postgresql+psycopg2://",
        creator=lambda: psycopg2.connect(DSN, options=settings.postgres_options),
    )
    try:
        with patch.object(migrations, "sync_engine", engine):
            migrations._drop_all_tables()
    finally:
        engine.dispose()


def test_force_rebuild_drops_leftover_staging_tables():
    command.upgrade(_alembic_config(), "head")
    _query_commit(f'CREATE TABLE "{SCHEMA}".cities500_new (id int)')

    _drop_with_app_engine()

    assert _query("SELECT 1 FROM pg_namespace WHERE nspname = %s", SCHEMA) == []


def test_force_rebuild_refuses_schema_with_foreign_objects():
    command.upgrade(_alembic_config(), "head")
    _query_commit(f'CREATE TABLE "{SCHEMA}".someone_elses (id int)')

    with pytest.raises(RuntimeError, match="someone_elses"):
        _drop_with_app_engine()

    assert APP_TABLES <= _tables(SCHEMA)


def _query_commit(query: str) -> None:
    conn = psycopg2.connect(DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(query)
        conn.commit()
    finally:
        conn.close()
