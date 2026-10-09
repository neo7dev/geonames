"""Alembic environment for the app schema: tiers, roles, profiles and API keys.

A separate chain from the geonames one (migrations/env.py), with its version
table in the app schema, so FORCE_REBUILD dropping the geonames schema never
reruns or touches these migrations. Select it with `alembic -n app ...`.
"""

from alembic import context
from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.engine import Connection

import geonames_server.database.models.accounts  # noqa: F401 — register the models
from geonames_server.config.config import settings
from geonames_server.database.models.app_base import APP_SCHEMA, AppBase

config = context.config

# use the application's database unless the caller supplied a URL
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.alembic_url)

target_metadata = AppBase.metadata


def include_name(name, type_, parent_names) -> bool:
    # Autogenerate compares the app schema only, never Supabase's or geonames'.
    if type_ == "schema":
        return name == APP_SCHEMA
    return True


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    # profiles.id -> auth.users(id) exists only on Supabase and is not on the model.
    return not (
        type_ == "foreign_key_constraint" and obj.referred_table.schema == "auth"
    )


_context_options = {
    "target_metadata": target_metadata,
    "version_table_schema": APP_SCHEMA,
    "include_schemas": True,
    "include_name": include_name,
    "include_object": include_object,
}


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_context_options,
    )

    with context.begin_transaction():
        context.execute(f"CREATE SCHEMA IF NOT EXISTS {APP_SCHEMA}")
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    quoted_schema = connection.dialect.identifier_preparer.quote(APP_SCHEMA)
    connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {quoted_schema}"))
    connection.commit()

    context.configure(connection=connection, **_context_options)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        do_run_migrations(connection)

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
