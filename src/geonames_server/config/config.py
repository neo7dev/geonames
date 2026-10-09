import re
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, override

from dotenv import find_dotenv
from pydantic import Field, SecretStr, computed_field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL

from geonames_server.log.mylogger import get_logger

log = get_logger()

_SCHEMA_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")

# Supabase Auth (GoTrue) needs a JWT secret of at least 32 characters.
_MIN_JWT_SECRET_LENGTH = 32

# Schemas owned by Postgres or Supabase. postgres_schema must not be one of them.
_RESERVED_SCHEMAS = frozenset(
    {
        "public",
        "information_schema",
        "auth",
        "storage",
        "extensions",
        "graphql",
        "graphql_public",
        "realtime",
        "_realtime",
        "_analytics",
        "vault",
        "pgsodium",
        "pgsodium_masks",
        "pgbouncer",
        "net",
        "cron",
        "pgmq",
        # User tables (tiers, roles, profiles, API keys); see database/models/app_base.py
        "app",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=find_dotenv(),  # Load .env file if it exists
        env_file_encoding="utf-8",
        extra="ignore",  # Ignore extra fields (shared .env with docker-compose)
        case_sensitive=False,
    )

    @property
    def app_name(self) -> str:
        return "Geonames Server"

    @property
    def app_version(self) -> str:
        try:
            __version__ = version("geonames_server")
            return __version__
        except PackageNotFoundError:
            return "0.1.0"  # Default version if package metadata is not available

    geonames_url: str = Field(
        default="https://download.geonames.org/export/dump/",
        description="Base URL for GeoNames data dumps",
    )

    root_dir: Path = Field(
        default=Path(find_dotenv()).parent,
        description="Root directory of the project",
    )
    # root_dir: Path = Field(default=Path(__file__).resolve().parent.parent)

    log_level: str = Field(
        default="INFO",
        description="Logging level for the application",
    )

    max_results: int = Field(
        default=1000,
        ge=1,
        le=1000000,
        description="Maximum number of results to return for list and filter endpoints",
    )

    postgres_user: str = Field(default="dev", description="PostgreSQL username")
    postgres_password: str = Field(default="dev", description="PostgreSQL password")
    postgres_host: str = Field(default="postgres", description="PostgreSQL host")
    postgres_port: int = Field(default=5432, description="PostgreSQL port")
    postgres_db: str = Field(default="devdb", description="PostgreSQL database name")
    postgres_schema: str = Field(
        default="geonames",
        description="Schema holding the geonames tables, kept out of Supabase's public schema",
    )

    redis_url: str | None = Field(
        default=None,
        description="Redis URL, e.g. redis://redis:6379/0. Reported by /health when set",
    )

    jwt_secret: SecretStr | None = Field(
        default=None,
        description="Supabase Auth JWT secret, used to verify login tokens on /portal. Required at startup",
    )

    auth_cache_seconds: int = Field(
        default=60,
        ge=0,
        description="Seconds an API key lookup is cached; role, tier and Studio revoke changes apply within this. 0 turns the cache off",
    )
    dev_seed_users: bool = Field(
        default=False,
        description="Allow `make seed-users` to create dev users with known passwords and API keys. Dev only",
    )

    enabled_datasets: Annotated[list[str], NoDecode] = Field(
        default=[
            "allcountries",
            "cities500",
            "cities1000",
            "cities5000",
            "cities15000",
        ],
        description="List of dataset names to download and sync",
    )

    sync_schedule_hours: Annotated[list[int], NoDecode] = Field(
        default=[3, 15],
        description="UTC hours at which to run daily modification/deletion checks",
    )

    force_rebuild: bool = Field(
        default=False,
        description="Force a full rebuild of all datasets on next startup",
    )

    @computed_field
    @property
    def postges_url(self) -> str:
        # URL.create percent-encodes the parts, so a password with @, : or / works.
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)

    @property
    def alembic_url(self) -> str:
        """postges_url escaped for Alembic's config, where ConfigParser reads % as interpolation."""
        return self.postges_url.replace("%", "%%")

    @property
    def postgres_options(self) -> str:
        """libpq ``options`` that resolve unqualified table names in ``postgres_schema``.

        Every connection (SQLAlchemy engines and raw psycopg2) must pass this, or
        unqualified SQL would read and write Supabase's ``public`` schema instead.
        """
        return f"-c search_path={self.postgres_schema}"

    @computed_field
    @property
    def debug(self) -> bool:
        return self.log_level.strip().upper() == "DEBUG"

    @computed_field
    @property
    def cache_dir(self) -> Path:
        return self.root_dir / "data" / "cache"

    @computed_field
    @property
    def data_dir(self) -> Path:
        return self.root_dir / "data" / "geonames"

    @computed_field
    @property
    def project_dir(self) -> Path:
        return self.root_dir / "src" / "geonames_server"

    @computed_field
    @property
    def gostatic_dir(self) -> Path:
        return self.root_dir / "gostatic"

    @computed_field
    @property
    def alembic_ini(self) -> Path:
        return self.root_dir / "alembic.ini"

    @computed_field
    @property
    def logging_config_file(self) -> Path:
        return self.project_dir / "logger_config" / "logger_config.json"

    @field_validator("enabled_datasets", mode="before")
    @classmethod
    def validate_enabled_datasets(cls, value):
        if isinstance(value, str):
            # Split comma-separated string into list
            return [item.strip() for item in value.split(",") if item.strip()]
        elif isinstance(value, list):
            # Ensure all items are strings
            if all(isinstance(item, str) for item in value):
                return value
        raise ValueError(
            "enabled_datasets must be a comma-separated string or a list of strings"
        )

    @field_validator("postgres_schema")
    @classmethod
    def validate_postgres_schema(cls, value: str) -> str:
        # postgres_options passes the name to search_path unquoted, so only names that
        # Postgres does not case-fold or need quoting resolve to the same schema.
        if not _SCHEMA_NAME.fullmatch(value):
            raise ValueError(
                f"postgres_schema {value!r} must be a lowercase identifier: "
                "letters a-z, digits and underscores, not starting with a digit, "
                "at most 63 characters"
            )
        # FORCE_REBUILD drops this schema with CASCADE, so it must belong to the app alone.
        if value in _RESERVED_SCHEMAS or value.startswith(("pg_", "supabase_")):
            raise ValueError(
                f"postgres_schema {value!r} is a Postgres or Supabase schema; "
                "use a schema owned by geonames-server alone"
            )
        return value

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        # `JWT_SECRET =` in .env (as shipped in .env.example) counts as not set,
        # so the startup check rejects it instead of every login failing later.
        secret = value.get_secret_value().strip()
        if not secret:
            return None
        if len(secret) < _MIN_JWT_SECRET_LENGTH:
            raise ValueError(
                f"jwt_secret must be at least {_MIN_JWT_SECRET_LENGTH} characters; "
                "generate one with `make supabase-keys`"
            )
        return SecretStr(secret)

    @field_validator("sync_schedule_hours", mode="before")
    @classmethod
    def validate_sync_schedule_hours(cls, value):
        if isinstance(value, str):
            return [int(item.strip()) for item in value.split(",") if item.strip()]
        elif isinstance(value, list):
            return [int(item) for item in value]
        raise ValueError(
            "sync_schedule_hours must be a comma-separated string or a list of ints"
        )

    @override
    def model_post_init(self, context):
        log.debug(
            "Settings initialised",
            extra={
                "app_name": self.app_name,
                "log_level": self.log_level,
                "debug": self.debug,
                "root_dir": self.root_dir,
                "cache_dir": self.cache_dir,
                "data_dir": self.data_dir,
                "project_dir": self.project_dir,
                "gostatic_dir": self.gostatic_dir,
                "logging_config_file": self.logging_config_file,
                "geonames_url": self.geonames_url,
                "enabled_datasets": self.enabled_datasets,
                "sync_schedule_hours": self.sync_schedule_hours,
                "force_rebuild": self.force_rebuild,
            },
        )
        return super().model_post_init(context)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
