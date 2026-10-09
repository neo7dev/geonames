"""Who is calling: API keys for data routes and /sync, login tokens for /portal.

API keys are sent in the X-API-Key header. Login tokens are the access tokens
Supabase Auth (GoTrue) issues, sent as ``Authorization: Bearer``. Neither is
accepted in the other's place.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.api.dependencies import get_session
from geonames_server.config.config import settings
from geonames_server.database.models.accounts import ApiKey, Profile, Role, Tier

API_KEY_PREFIX = "gn_"
# Shown in key lists: "gn_" plus the first 8 random characters
_DISPLAY_PREFIX_LENGTH = len(API_KEY_PREFIX) + 8

# GoTrue's audience and role claim for signed-in users. The public ANON_KEY and
# SERVICE_ROLE_KEY are signed with the same secret but have neither.
JWT_AUDIENCE = "authenticated"
JWT_ROLE = "authenticated"
JWT_LEEWAY_SECONDS = 30

_api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="API key created on /portal/keys",
)
_bearer = HTTPBearer(
    auto_error=False,
    description="Supabase Auth access token",
)


@dataclass(frozen=True)
class Principal:
    """The user behind a request, with their role's tier limits (None = unlimited)."""

    user_id: uuid.UUID
    email: str | None
    key_id: uuid.UUID | None  # None when authenticated with a login token
    role: str
    is_admin: bool
    tier: str
    per_minute: int
    per_hour: int | None
    per_month: int | None
    max_keys: int | None


def hash_api_key(key: str) -> str:
    # Keys are 256 random bits, so a fast hash is enough; it also allows an
    # indexed lookup by hash.
    return hashlib.sha256(key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Return a new key, its display prefix and its hash."""
    key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return key, key[:_DISPLAY_PREFIX_LENGTH], hash_api_key(key)


def _principal_select(*columns) -> Select:
    return (
        select(
            Profile.id.label("user_id"),
            Profile.email,
            Role.name.label("role"),
            Role.is_admin,
            Tier.name.label("tier"),
            Tier.per_minute,
            Tier.per_hour,
            Tier.per_month,
            Tier.max_keys,
            *columns,
        )
        .select_from(Profile)
        .join(Role, Role.name == Profile.role)
        .join(Tier, Tier.name == Role.tier)
    )


def _unauthorized(detail: str, scheme: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": scheme},
    )


async def require_api_key(
    request: Request,
    key: str | None = Depends(_api_key_header),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    if not key:
        raise _unauthorized(
            "Missing API key: send it in the X-API-Key header", "ApiKey"
        )
    row = (
        await session.execute(
            _principal_select(ApiKey.id.label("key_id"))
            .join(ApiKey, ApiKey.user_id == Profile.id)
            .where(ApiKey.key_hash == hash_api_key(key), ApiKey.revoked_at.is_(None))
        )
    ).one_or_none()
    if row is None:
        raise _unauthorized("Invalid or revoked API key", "ApiKey")
    principal = Principal(**row._mapping)
    request.state.principal = principal
    return principal


async def require_admin(principal: Principal = Depends(require_api_key)) -> Principal:
    if not principal.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint needs an admin account",
        )
    return principal


def _login_user_id(token: str) -> uuid.UUID:
    """Verify a Supabase Auth access token and return its user id."""
    secret = settings.jwt_secret
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login is not configured on this server",
        )
    invalid = _unauthorized("Invalid or expired login token", "Bearer")
    try:
        claims = jwt.decode(
            token,
            secret.get_secret_value(),
            algorithms=["HS256"],
            audience=JWT_AUDIENCE,
            leeway=JWT_LEEWAY_SECONDS,
            options={"require": ["exp", "sub", "aud"]},
        )
        user_id = uuid.UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise invalid from None
    if claims.get("role") != JWT_ROLE or claims.get("is_anonymous"):
        raise invalid
    return user_id


async def require_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    if credentials is None:
        raise _unauthorized(
            "Missing login token: send it as Authorization: Bearer <token>", "Bearer"
        )
    user_id = _login_user_id(credentials.credentials)
    row = (
        await session.execute(_principal_select().where(Profile.id == user_id))
    ).one_or_none()
    if row is None:
        # The signup trigger creates a profile for every user, so this means
        # the user was deleted, or signed up while the trigger was missing.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has no profile; ask an admin to add one",
        )
    principal = Principal(key_id=None, **row._mapping)
    request.state.principal = principal
    return principal
