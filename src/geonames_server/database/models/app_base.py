from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Schema for user data. It has its own Alembic chain (migrations/app), so
# FORCE_REBUILD, which drops the geonames schema, never touches it.
APP_SCHEMA = "app"


class AppBase(DeclarativeBase):
    """Base for user tables: tiers, roles, profiles and API keys.

    Separate from ``Base`` so the geonames migrations, the FORCE_REBUILD checks
    and autogenerate for the geonames schema never see these tables.
    """

    metadata = MetaData(schema=APP_SCHEMA)
