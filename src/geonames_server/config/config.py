import re
import tomllib
from functools import cache, lru_cache
from pathlib import Path
from typing import Annotated

from dotenv import find_dotenv
from pydantic import Field, SecretStr, computed_field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL

from geonames_server.log.mylogger import get_logger, set_log_level

log = get_logger()

_SCHEMA_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")

# The app version lives only in pyproject.toml, which the image also ships (in /app,
# next to src/). The project is not installed as a package, so there is no package
# metadata to read it from.
_PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


@cache
def _project_version() -> str:
    with _PYPROJECT.open("rb") as f:
        return tomllib.load(f)["project"]["version"]


_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# Supabase Auth (GoTrue) needs a JWT secret of at least 32 characters.
_MIN_JWT_SECRET_LENGTH = 32
# An http(s) origin: host name or [IPv6] address, optional port, optional "/".
_ORIGIN = (
    r"https?://(?:[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*|\[[0-9A-Fa-f:.]+\])"
    r"(?::\d{1,5})?/?"
)

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
        # Also the Docker image's tag (Makefile VERSION)
        return _project_version()

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
        description="Logging level: DEBUG, INFO, WARNING, ERROR or CRITICAL (any case). DEBUG also logs SQL statements",
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
    usage_flush_seconds: int = Field(
        default=300,
        ge=10,
        description="Seconds between copies of the request counts from Redis to Postgres",
    )
    supabase_public_url: str | None = Field(
        default=None,
        description="Supabase (Kong) URL as users' browsers reach it, e.g. https://auth.example.com. The web UI calls Supabase Auth there",
    )
    anon_key: str | None = Field(
        default=None,
        description="Supabase anon key, handed to the web UI for Supabase Auth calls. Public by design",
    )
    password_min_length: int = Field(
        default=8,
        ge=6,
        description="Shortest password Supabase Auth accepts (GOTRUE_PASSWORD_MIN_LENGTH); the web UI checks it on signup",
    )
    gotrue_url: str = Field(
        default="http://auth:9999",
        description="Supabase Auth (GoTrue) URL as the server reaches it, without Kong. Used to create and delete users on the Administration page",
    )
    admin_email: str | None = Field(
        default=None,
        description="Email of the first admin account, created on the first start while no admin exists. Ignored once one does",
    )
    admin_password: SecretStr | None = Field(
        default=None,
        description="Password of the first admin account (ADMIN_EMAIL). Only read while no admin exists; remove it from .env afterwards",
    )
    ui_dir: Path | None = Field(
        default=None,
        description="Built web UI (index.html and assets/) served at /ui. Defaults to frontend/dist under root_dir",
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

    @property
    def ui_root(self) -> Path:
        return self.ui_dir or self.root_dir / "frontend" / "dist"

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

    @field_validator("supabase_public_url", "anon_key", "admin_email")
    @classmethod
    def blank_is_unset(cls, value: str | None) -> str | None:
        # `ANON_KEY =` in .env (as shipped in .env.example) counts as not set.
        if value is None or not value.strip():
            return None
        return value.strip()

    @field_validator("admin_password")
    @classmethod
    def blank_password_is_unset(cls, value: SecretStr | None) -> SecretStr | None:
        # `ADMIN_PASSWORD =` in .env counts as not set. Not stripped: spaces may
        # be part of the password.
        if value is None or not value.get_secret_value():
            return None
        return value

    @field_validator("supabase_public_url")
    @classmethod
    def validate_supabase_public_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # The web UI's CSP lists it in connect-src, which needs a plain origin:
        # a host name or [IPv6] address and an optional port, nothing else
        # (no user:password@, ?query, #fragment or * wildcard).
        if not re.fullmatch(_ORIGIN, value):
            raise ValueError(
                f"supabase_public_url {value!r} must be an http(s) origin, "
                "e.g. https://auth.example.com, without a path"
            )
        return value.rstrip("/")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in _LOG_LEVELS:
            raise ValueError(
                f"log_level must be one of {', '.join(_LOG_LEVELS)}, not {value!r}"
            )
        return level

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
set_log_level(settings.log_level)
log.debug(
    "Settings initialised",
    extra={
        "app_name": settings.app_name,
        "log_level": settings.log_level,
        "debug": settings.debug,
        "root_dir": settings.root_dir,
        "cache_dir": settings.cache_dir,
        "data_dir": settings.data_dir,
        "project_dir": settings.project_dir,
        "gostatic_dir": settings.gostatic_dir,
        "logging_config_file": settings.logging_config_file,
        "geonames_url": settings.geonames_url,
        "enabled_datasets": settings.enabled_datasets,
        "sync_schedule_hours": settings.sync_schedule_hours,
        "force_rebuild": settings.force_rebuild,
    },
)
