from collections.abc import Iterator
from contextlib import contextmanager

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text

from geonames_server.config.config import settings
from geonames_server.database.auth_link import link_profiles_to_auth
from geonames_server.database.models.base import Base
from geonames_server.database.session import sync_engine
from geonames_server.log.mylogger import get_logger

log = get_logger()

_alembic_ini = settings.alembic_ini

# pg_advisory_lock key held while migrating, so the server and `make
# seed-users` starting together do not both create the same tables.
_MIGRATION_LOCK_KEY = 0x67656F6E616D6573  # "geonames"


def _app_relation_names() -> set[str]:
    """Tables the app creates: models, Alembic's version table and load staging tables."""
    names = set(Base.metadata.tables)
    staging = {f"{name}{suffix}" for name in names for suffix in ("_new", "_old")}
    return names | staging | {"alembic_version"}


def _foreign_objects(conn: Connection, schema: str) -> list[str]:
    """Relations and functions in *schema* that this app did not create."""
    relations = conn.execute(
        text(
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND c.relkind IN ('r', 'p', 'v', 'm', 'f')"
        ),
        {"schema": schema},
    ).scalars()
    functions = conn.execute(
        text(
            "SELECT p.proname FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = :schema"
        ),
        {"schema": schema},
    ).scalars()
    app_names = _app_relation_names()
    return sorted(
        {name for name in relations if name not in app_names} | set(functions)
    )


def _drop_all_tables() -> None:
    """Drop the app's schema, plus any tables left in public by older versions.

    Dropping the schema removes every table and the Alembic version table. It
    refuses if the schema holds anything the app did not create, so a schema
    shared with another service is never dropped. The legacy public tables go
    too, or migrations would move them back in.
    """
    import geonames_server.database.models  # noqa: F401 — ensure all models are registered

    log.warning(f"Force rebuild: dropping schema {settings.postgres_schema}")
    with sync_engine.begin() as conn:
        foreign = _foreign_objects(conn, settings.postgres_schema)
        if foreign:
            raise RuntimeError(
                f"Refusing to drop schema {settings.postgres_schema!r}: it holds "
                f"objects geonames-server did not create: {', '.join(foreign)}"
            )
        quote = conn.dialect.identifier_preparer.quote
        conn.execute(
            text(f"DROP SCHEMA IF EXISTS {quote(settings.postgres_schema)} CASCADE")
        )
        for name in [*Base.metadata.tables, "alembic_version"]:
            conn.execute(text(f"DROP TABLE IF EXISTS public.{quote(name)}"))
    log.info("All tables dropped")


def _check_search_path() -> None:
    """Fail fast if app connections do not resolve tables in the app's schema."""
    with sync_engine.connect() as conn:
        current = conn.execute(text("SELECT current_schema()")).scalar()
    if current != settings.postgres_schema:
        raise RuntimeError(
            f"Database connections resolve tables in schema {current!r}, "
            f"expected {settings.postgres_schema!r}"
        )


def _alembic_config(section: str = "alembic") -> Config:
    """Config for one migration chain: ``alembic`` (geonames) or ``app`` (user data)."""
    cfg = Config(str(_alembic_ini), ini_section=section)
    cfg.set_main_option("sqlalchemy.url", settings.postges_url)
    return cfg


@contextmanager
def _migration_lock() -> Iterator[None]:
    """Wait for, then hold, the database-wide migration lock."""
    with sync_engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _MIGRATION_LOCK_KEY})
        conn.commit()
        try:
            yield
        finally:
            conn.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": _MIGRATION_LOCK_KEY}
            )
            conn.commit()


def _upgrade_app_schema() -> None:
    command.upgrade(_alembic_config("app"), "head")
    with sync_engine.begin() as conn:
        if not link_profiles_to_auth(conn):
            log.warning("auth.users not found: app.profiles is not linked to Supabase Auth")


def upgrade_app_schema() -> None:
    """Migrate the app (user data) chain, then link profiles to Supabase Auth if needed."""
    with _migration_lock():
        _upgrade_app_schema()


def db_migrations(force: bool = False) -> None:
    """Run Alembic migrations: the geonames chain, then the app (user data) chain.

    If *force* is True, drop the geonames tables first so their migrations run
    from scratch (full rebuild). User data in the app schema is never dropped.
    """
    log.info(
        "Running database migrations",
        extra={"alembic_ini": str(_alembic_ini), "force": force},
    )

    try:
        with _migration_lock():
            if force:
                _drop_all_tables()
            command.upgrade(_alembic_config(), "head")
            _upgrade_app_schema()
        _check_search_path()
        log.info("Database migrations complete")
    except Exception as e:
        log.exception("Database migrations failed")
        log.error(
            "Database migration failed, use the FORCE_REBUILD flag to build the database and the project."
        )
        raise e
