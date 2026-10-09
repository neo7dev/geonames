from alembic import context
from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.engine import Connection

# ensure all models are imported so Base.metadata is fully populated
import geonames_server.database.models  # noqa: F401
from geonames_server.config.config import settings
from geonames_server.database.models.base import Base

# from geonames_server.log.mylogger import setup_logging

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# use the application's database unless the caller supplied a URL
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.postges_url)

# Every table, including alembic_version, lives in this schema rather than Supabase's
# public schema, where anon/authenticated get access to every new table.
schema = settings.postgres_schema

# Always use the application's logging setup (Rich console + JSON file)
# instead of alembic.ini's basic fileConfig.  setup_logging() is
# idempotent, so this is safe whether called from the app or the CLI.

# setup_logging()

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def include_name(name, type_, parent_names) -> bool:
    # Autogenerate reflects the schema as the default one (schema None), where
    # Alembic does not recognise its own version table and would drop it.
    return not (type_ == "table" and name == "alembic_version")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=schema,
        include_name=include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


def _adopt_public_tables(connection: Connection, quoted_schema: str) -> None:
    """Move tables created before the schema split from public into the schema.

    Older versions created every table, alembic_version included, in public.
    Moving alembic_version along keeps the migration history, so Alembic carries
    on from the recorded revision instead of creating empty tables. Only this
    app's tables are moved; data, indexes and owned sequences move with them.
    """
    legacy = connection.execute(text("SELECT to_regclass('public.alembic_version')"))
    current = connection.execute(
        text("SELECT to_regclass(:name)"), {"name": f"{quoted_schema}.alembic_version"}
    )
    if legacy.scalar() is None or current.scalar() is not None:
        return

    quote = connection.dialect.identifier_preparer.quote
    for name in [*target_metadata.tables, "alembic_version"]:
        connection.execute(
            text(
                f"ALTER TABLE IF EXISTS public.{quote(name)} SET SCHEMA {quoted_schema}"
            )
        )


def do_run_migrations(connection: Connection) -> None:
    quoted_schema = connection.dialect.identifier_preparer.quote(schema)
    connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {quoted_schema}"))
    _adopt_public_tables(connection, quoted_schema)
    # Unqualified names in migrations resolve in the schema, as they do in the app.
    connection.execute(text(f"SET search_path TO {quoted_schema}"))
    connection.commit()

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=schema,
        include_name=include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

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
