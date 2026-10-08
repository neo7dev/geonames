from alembic import command
from alembic.config import Config
from sqlalchemy import text

from geonames_server.config.config import settings
from geonames_server.database.models.base import Base
from geonames_server.database.session import sync_engine
from geonames_server.log.mylogger import get_logger

log = get_logger()

_alembic_ini = settings.alembic_ini


def _drop_all_tables() -> None:
    """Drop all known tables and the Alembic version table."""
    import geonames_server.database.models  # noqa: F401 — ensure all models are registered

    log.warning("Force rebuild: dropping all tables")
    Base.metadata.drop_all(bind=sync_engine)
    log.info("All model tables dropped")

    # Drop Alembic's version tracking table so migrations start fresh
    with sync_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    log.info("Alembic version table dropped")


def db_migrations(force: bool = False) -> None:
    """Run Alembic migrations.

    If *force* is True, drop every table first so migrations run from
    scratch (full rebuild).
    """
    log.info(
        "Running database migrations",
        extra={"alembic_ini": str(_alembic_ini), "force": force},
    )

    if force:
        _drop_all_tables()

    try:
        alembic_cfg = Config(str(_alembic_ini))
        alembic_cfg.set_main_option("sqlalchemy.url", settings.postges_url)
        command.upgrade(alembic_cfg, "head")
        log.info("Database migrations complete")
    except Exception as e:
        log.exception("Database migrations failed")
        log.error(
            "Database migration failed, use the FORCE_REBUILD flag to build the database and the project."
        )
        raise e
