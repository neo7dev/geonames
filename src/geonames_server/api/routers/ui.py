"""The web UI: the built single-page app at /ui and the config it loads first.

Page routes (/ui/keys, /ui/auth/callback, ...) belong to the app's client-side
router, so any path without a file extension gets index.html. A missing file
(a path with an extension) is a real 404, so a stale asset URL fails loudly.
"""

from pathlib import Path, PurePosixPath

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from geonames_server.config.config import settings

router = APIRouter(include_in_schema=False)

# Vite puts content-hashed files here; a new build gets new names.
_ASSETS = "assets/"
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
        },
        headers=_headers(),
    )


@router.get("/ui")
@router.get("/ui/{path:path}")
async def ui(path: str = "") -> Response:
    root = settings.ui_root.resolve()
    index = root / "index.html"
    if not index.is_file():
        return _not_found("UI not built: run make ui-build")

    found = _file(root, path) if path else None
    if found is not None:
        cache = _IMMUTABLE if path.startswith(_ASSETS) else _NO_CACHE
        return FileResponse(found, headers=_headers(cache))
    if PurePosixPath(path).suffix:
        return _not_found(f"/ui/{path} not found")
    return FileResponse(index, headers=_headers())
