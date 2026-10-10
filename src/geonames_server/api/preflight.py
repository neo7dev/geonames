"""Startup checks, run before the server touches the database or anything else.

Required: the database, Redis (when REDIS_URL is set), Supabase Auth and
JWT_SECRET. Each service gets a few attempts, since compose may still be
starting it; if any stays unreachable the server stops and lists every
failure, and Docker's restart policy tries again.

Optional, warned about but never stopping the server: SMTP (Supabase Auth's
emails) and the web UI's Supabase settings. SMTP gets different warnings for
"not configured" and "configured but not working".
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

import httpx
import psycopg2
from redis.asyncio import Redis

from geonames_server.config.config import settings
from geonames_server.log.mylogger import get_logger

log = get_logger()

ATTEMPTS = 5
RETRY_SECONDS = 2.0
_TIMEOUT_SECONDS = 5

_EMAIL_FEATURES = (
    'confirming a signup, "Forgot password?" and changing an email address'
)
_SMTP_SETTINGS = "SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS and SMTP_ADMIN_EMAIL"
_RESTART = "then run `docker compose up -d` to restart Supabase Auth and the server"


class PreflightError(Exception):
    """A required service or setting is missing; the server cannot start."""


class SmtpStatus(StrEnum):
    not_configured = "not_configured"
    ok = "ok"
    failed = "failed"


@dataclass(frozen=True)
class _Check:
    name: str
    target: str
    run: Callable[[], Awaitable[None]]


def _describe(e: BaseException) -> str:
    return str(e).strip() or type(e).__name__


async def _with_retries(
    check: _Check, attempts: int | None = None, delay: float | None = None
) -> str | None:
    """None once the check passes, else the last error."""
    attempts = ATTEMPTS if attempts is None else attempts
    delay = RETRY_SECONDS if delay is None else delay
    error = None
    for attempt in range(1, attempts + 1):
        try:
            await check.run()
            return None
        except Exception as e:
            error = _describe(e)
            if attempt < attempts:
                log.info(
                    f"{check.name} not reachable yet ({error}); "
                    f"retrying in {delay:g} s ({attempt}/{attempts})"
                )
                await asyncio.sleep(delay)
    return error


def _connect_database() -> None:
    psycopg2.connect(
        user=settings.postgres_user,
        password=settings.postgres_password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        connect_timeout=_TIMEOUT_SECONDS,
    ).close()


async def _check_database() -> None:
    await asyncio.to_thread(_connect_database)


async def _check_redis(url: str) -> None:
    client = Redis.from_url(
        url,
        socket_connect_timeout=_TIMEOUT_SECONDS,
        socket_timeout=_TIMEOUT_SECONDS,
    )
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _check_auth(url: str) -> None:
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        response = await client.get(f"{url}/health")
    response.raise_for_status()


def _redis_target(url: str) -> str:
    # Host and port only: the URL may hold a password.
    parts = urlsplit(url)
    return f"{parts.hostname}:{parts.port or 6379}"


def check_smtp() -> tuple[SmtpStatus, str | None]:
    """Connect and log in the way Supabase Auth would, without sending anything.

    Supabase Auth uses TLS from the start on port 465 and STARTTLS when the
    server offers it, and sends a password only over an encrypted connection.
    """
    host, port = settings.smtp_host, settings.smtp_port
    if host is None:
        return SmtpStatus.not_configured, None
    if settings.smtp_admin_email is None:
        return SmtpStatus.failed, "SMTP_ADMIN_EMAIL (the sender address) is empty"

    context = ssl.create_default_context()
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(
                host, port, timeout=_TIMEOUT_SECONDS, context=context
            )
        else:
            server = smtplib.SMTP(host, port, timeout=_TIMEOUT_SECONDS)
        with server:
            server.ehlo()
            encrypted = port == 465
            if not encrypted and server.has_extn("starttls"):
                server.starttls(context=context)
                server.ehlo()
                encrypted = True
            if settings.smtp_user:
                if not encrypted:
                    return SmtpStatus.failed, (
                        f"{host}:{port} offers no TLS, and Supabase Auth does not "
                        "send the SMTP password over an unencrypted connection"
                    )
                password = settings.smtp_pass
                server.login(
                    settings.smtp_user,
                    password.get_secret_value() if password else "",
                )
    except (OSError, smtplib.SMTPException) as e:
        return SmtpStatus.failed, f"{host}:{port}: {_describe(e)}"
    return SmtpStatus.ok, None


def _warn_smtp(status: SmtpStatus, error: str | None) -> None:
    if status is SmtpStatus.ok:
        log.info(f"Preflight: SMTP ok ({settings.smtp_host}:{settings.smtp_port})")
        return

    signups = ""
    if not settings.disable_signup and not settings.enable_email_autoconfirm:
        signups = (
            " New signups cannot be confirmed, so they cannot log in: set up SMTP, "
            "or set DISABLE_SIGNUP=true (admins create accounts) or "
            "ENABLE_EMAIL_AUTOCONFIRM=true (signups need no confirmation)."
        )
    if status is SmtpStatus.not_configured:
        log.warning(
            "SMTP is not configured (SMTP_HOST is empty), so no emails are sent. "
            f"Everything else works, but not {_EMAIL_FEATURES}: Supabase Auth "
            f"answers as if it had sent the email.{signups} To turn email on, set "
            f"{_SMTP_SETTINGS} in .env, {_RESTART}. Accounts created on the "
            "Administration page need no email."
        )
    else:
        log.warning(
            f"SMTP is configured but not working: {error}. Everything else works, "
            f"but {_EMAIL_FEATURES} fail with an error until it is fixed.{signups} "
            f"Check {_SMTP_SETTINGS} in .env, {_RESTART}."
        )


def _warn_web_ui() -> None:
    missing = [
        name
        for name, value in (
            ("SUPABASE_PUBLIC_URL", settings.supabase_public_url),
            ("ANON_KEY", settings.anon_key),
        )
        if value is None
    ]
    if missing:
        log.warning(
            f"{' and '.join(missing)} not set: the web UI cannot log anyone in "
            "(/ui/config.json answers 503). The API works with API keys."
        )


async def run_preflight() -> None:
    """Check the required services, then warn about optional ones.

    Raises PreflightError naming every failed requirement.
    """
    log.info("Preflight: checking the database, Redis, Supabase Auth and SMTP")
    failures = []
    if settings.jwt_secret is None:
        failures.append(
            "JWT_SECRET is not set, so login tokens cannot be checked; "
            "generate it with `make supabase-keys`"
        )

    checks = [
        _Check(
            "Database",
            f"{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}",
            _check_database,
        ),
        _Check(
            "Supabase Auth",
            settings.gotrue_url,
            lambda: _check_auth(settings.gotrue_url),
        ),
    ]
    if settings.redis_url:
        url = settings.redis_url
        checks.insert(1, _Check("Redis", _redis_target(url), lambda: _check_redis(url)))
    else:
        log.warning(
            "REDIS_URL is not set: rate limits are off and usage is not counted"
        )

    # Concurrently, so the retries overlap. SMTP is optional and runs alongside.
    smtp = asyncio.create_task(asyncio.to_thread(check_smtp))
    errors = await asyncio.gather(*(_with_retries(check) for check in checks))
    for check, error in zip(checks, errors):
        if error is None:
            log.info(f"Preflight: {check.name} ok ({check.target})")
        else:
            failures.append(f"{check.name} at {check.target} is not reachable: {error}")

    if failures:
        smtp.cancel()
        for failure in failures:
            log.critical(f"Preflight failed: {failure}")
        raise PreflightError("; ".join(failures))

    _warn_smtp(*await smtp)
    _warn_web_ui()
