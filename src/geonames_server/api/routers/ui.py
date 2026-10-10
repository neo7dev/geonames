"""The web UI: the built single-page app at /ui and the config it loads first.

Page routes (/ui/keys, /ui/auth/callback, ...) belong to the app's client-side
router, so any path without a file extension gets index.html. A missing file
(a path with an extension) is a real 404, so a stale asset URL fails loudly.
"""

import time
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import APIRouter, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

from geonames_server.api.preflight import SmtpStatus, smtp_status
from geonames_server.config.config import settings
from geonames_server.log.mylogger import get_logger

log = get_logger()

router = APIRouter(include_in_schema=False)

# Vite puts content-hashed files here; a new build gets new names.
_ASSETS = "assets"
_IMMUTABLE = "public, max-age=31536000, immutable"
_NO_CACHE = "no-cache"


def _headers(cache_control: str = _NO_CACHE) -> dict[str, str]:
    # The login session lives in localStorage, so scripts may only come from the
    # app itself: no inline scripts. Radix sets style attributes, hence
    # 'unsafe-inline' for styles only.
    connect = "'self'"
    if settings.supabase_public_url:
        connect += f" {settings.supabase_public_url}"
    csp = "; ".join(
        [
            "default-src 'self'",
            f"connect-src {connect}",
            "img-src 'self' data:",
            "style-src 'self' 'unsafe-inline'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
    )
    return {
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": cache_control,
    }


def _not_found(detail: str) -> Response:
    return JSONResponse({"detail": detail}, status_code=404, headers=_headers())


def _file(root: Path, path: str) -> Path | None:
    """The file at `path` under `root`, or None if missing or outside it."""
    # is_file() re-raises some OSErrors too, e.g. ENAMETOOLONG for a long segment.
    try:
        candidate = (root / path).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return candidate
    except (OSError, ValueError):
        pass
    return None


@router.get("/")
async def root() -> Response:
    return RedirectResponse("/ui/")


@router.get("/ui/config.json")
async def ui_config() -> Response:
    if settings.supabase_public_url is None or settings.anon_key is None:
        return JSONResponse(
            {"detail": "UI is not configured: set SUPABASE_PUBLIC_URL and ANON_KEY"},
            status_code=503,
            headers=_headers(),
        )
    return JSONResponse(
        {
            "supabase_url": settings.supabase_public_url,
            "anon_key": settings.anon_key,
            "password_min_length": settings.password_min_length,
            # SMTP as found at startup: ok, not_configured, failed, or unknown
            # before the check ran. Only the status: the error may name the server.
            "email": smtp_status() or "unknown",
        },
        headers=_headers(),
    )


class EmailFeature(StrEnum):
    signup = "signup"
    password_reset = "password_reset"
    email_change = "email_change"
    resend_confirmation = "resend_confirmation"


_FEATURES = {
    EmailFeature.signup: "to sign up",
    EmailFeature.password_reset: 'to reset a password ("Forgot password?")',
    EmailFeature.email_change: "to change an email address",
    EmailFeature.resend_confirmation: "to resend a confirmation email",
}


class EmailProblem(BaseModel):
    feature: EmailFeature
    # unavailable: the UI stopped it, as SMTP is off or broken. send_failed:
    # Supabase Auth tried and could not send the email.
    reason: Literal["unavailable", "send_failed"]


# Each kind of report is logged at most this often, so the endpoint (open to
# anyone, like the pages) cannot flood the logs.
REPORT_INTERVAL_SECONDS = 60
_last_report: dict[tuple[EmailFeature, str], float] = {}


@router.post("/ui/email-problem", status_code=status.HTTP_204_NO_CONTENT)
async def email_problem(report: EmailProblem) -> Response:
    """The web UI reports a feature that needed email, for the server's log."""
    smtp = smtp_status()
    feature = _FEATURES[report.feature]
    if report.reason == "send_failed":
        message = (
            f"A user tried {feature} in the web UI, but Supabase Auth could not "
            "send the email. Check SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS and "
            "SMTP_ADMIN_EMAIL in .env and Supabase Auth's log (`docker compose "
            "logs auth`), then run `docker compose up -d`."
        )
    elif smtp is SmtpStatus.not_configured:
        message = (
            f"A user tried {feature} in the web UI, but SMTP is not configured "
            "(SMTP_HOST is empty), so no email can be sent. Set SMTP_HOST, "
            "SMTP_PORT, SMTP_USER, SMTP_PASS and SMTP_ADMIN_EMAIL in .env, then "
            "run `docker compose up -d`."
        )
    elif smtp is SmtpStatus.failed:
        message = (
            f"A user tried {feature} in the web UI, but SMTP is not working (see "
            "the SMTP warning at startup). Fix the SMTP settings in .env, then run "
            "`docker compose up -d`."
        )
    else:
        # Email works as far as the server knows; nothing to report.
        message = None

    key = (report.feature, report.reason)
    now = time.monotonic()
    if (
        message
        and now - _last_report.get(key, float("-inf")) >= REPORT_INTERVAL_SECONDS
    ):
        _last_report[key] = now
        log.warning(message)
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=_headers())


@router.get("/ui")
@router.get("/ui/{path:path}")
async def ui(path: str = "") -> Response:
    root = settings.ui_root.resolve()
    index = root / "index.html"
    if not index.is_file():
        return _not_found("UI not built: run make ui-build")

    found = _file(root, path) if path else None
    if found is not None:
        # By where the file is, not the path: assets/../index.html is no asset.
        cache = _IMMUTABLE if found.is_relative_to(root / _ASSETS) else _NO_CACHE
        return FileResponse(found, headers=_headers(cache))
    if PurePosixPath(path).suffix:
        return _not_found(f"/ui/{path} not found")
    return FileResponse(index, headers=_headers())
