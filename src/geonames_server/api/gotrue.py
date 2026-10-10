"""Supabase Auth's (GoTrue's) admin API: create and delete users.

Users live in auth.users, which only GoTrue writes; the signup trigger then
creates or removes their profile. Called directly on settings.gotrue_url, not
through Kong, with a short-lived service_role token signed with JWT_SECRET.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt

from geonames_server.config.config import settings

_TIMEOUT_SECONDS = 10


class GoTrueError(Exception):
    """GoTrue refused the request or could not be reached."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        # GoTrue's status, None when it could not be reached
        self.status_code = status_code


def service_token() -> str:
    """A short-lived service_role token, which GoTrue's admin API requires."""
    now = datetime.now(UTC)
    return jwt.encode(
        {"role": "service_role", "iat": now, "exp": now + timedelta(minutes=5)},
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.gotrue_url,
        headers={"Authorization": f"Bearer {service_token()}"},
        timeout=_TIMEOUT_SECONDS,
    )


def _message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text or f"Supabase Auth answered {resp.status_code}"
    # Newer GoTrue sends "msg", older "message" or "error_description"
    for field in ("msg", "message", "error_description", "error"):
        if isinstance(body.get(field), str):
            return body[field]
    return f"Supabase Auth answered {resp.status_code}"


async def _request(method: str, path: str, **kwargs) -> httpx.Response:
    try:
        async with _client() as client:
            resp = await client.request(method, path, **kwargs)
    except httpx.HTTPError as e:
        raise GoTrueError(f"Could not reach Supabase Auth: {e}") from e
    if resp.is_error:
        raise GoTrueError(_message(resp), resp.status_code)
    return resp


async def create_user(
    email: str, password: str, display_name: str | None = None
) -> uuid.UUID:
    """Create a confirmed user who can sign in with the password at once.

    The display name goes in as full_name metadata, which the signup trigger
    copies onto the profile, as for a signup through the web UI.
    """
    body = {"email": email, "password": password, "email_confirm": True}
    if display_name:
        body["user_metadata"] = {"full_name": display_name}
    resp = await _request("POST", "/admin/users", json=body)
    return uuid.UUID(resp.json()["id"])


async def delete_user(user_id: uuid.UUID) -> None:
    """Remove a user for good; the FK on app.profiles removes their profile and keys."""
    await _request("DELETE", f"/admin/users/{user_id}")
