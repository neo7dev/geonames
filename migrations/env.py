from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection

# ensure all models are imported so Base.metadata is fully populated
import geonames_server.database.models  # noqa: F401
from geonames_server.config.config import settings
from geonames_server.database.models.base import Base

# from geonames_server.log.mylogger import setup_logging

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# override sqlalchemy.url from application settings
config.set_main_option("sqlalchemy.url", settings.postges_url)

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
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

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
