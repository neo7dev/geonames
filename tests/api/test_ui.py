from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from geonames_server.api import preflight
from geonames_server.api.app import app
from geonames_server.api.preflight import SmtpStatus
from geonames_server.api.routers import ui
from geonames_server.config.config import settings

SUPABASE = "http://localhost:8000"
INDEX = "<!doctype html><title>Geonames</title>"
ASSET = "console.log('app')"
OUTSIDE = "must-not-be-served"


@pytest.fixture
def ui_dir(tmp_path):
    """A built UI in tmp_path/dist, with a file outside it that must stay unreachable."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX)
    (dist / "assets" / "app-abc123.js").write_text(ASSET)
    (dist / "theme-init.js").write_text("// theme")
    (tmp_path / "secret.txt").write_text(OUTSIDE)
    (tmp_path / "dist-other").mkdir()
    (tmp_path / "dist-other" / "x.js").write_text(OUTSIDE)
    return dist


@pytest.fixture(autouse=True)
def _settings(ui_dir):
    with (
        patch.object(settings, "ui_dir", ui_dir),
        patch.object(settings, "supabase_public_url", SUPABASE),
        patch.object(settings, "anon_key", "anon-key"),
        patch.object(settings, "password_min_length", 8),
    ):
        yield


@pytest_asyncio.fixture
async def client():
    # No lifespan: the UI routes need neither the database nor Redis.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def assert_ui_headers(response):
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert f"connect-src 'self' {SUPABASE}" in csp
    assert (
        "script-src" not in csp
    )  # falls back to default-src 'self': no inline scripts
    assert "frame-ancestors 'none'" in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


class TestPages:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/ui", "/ui/", "/ui/keys", "/ui/auth/callback"])
    async def test_serve_index(self, client, path):
        response = await client.get(path)

        assert response.status_code == 200
        assert response.text == INDEX
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "no-cache"
        assert_ui_headers(response)

    @pytest.mark.asyncio
    async def test_root_redirects_to_ui(self, client):
        response = await client.get("/")

        assert response.status_code == 307
        assert response.headers["location"] == "/ui/"

    @pytest.mark.asyncio
    async def test_need_no_api_key_and_stay_out_of_openapi(self, client):
        assert (await client.get("/ui/keys")).status_code == 200

        paths = (await client.get("/openapi.json")).json()["paths"]
        assert not [p for p in paths if p == "/" or p.startswith("/ui")]


class TestFiles:
    @pytest.mark.asyncio
    async def test_hashed_asset_is_cached_for_good(self, client):
        response = await client.get("/ui/assets/app-abc123.js")

        assert response.status_code == 200
        assert response.text == ASSET
        assert "javascript" in response.headers["content-type"]
        assert (
            response.headers["cache-control"] == "public, max-age=31536000, immutable"
        )
        assert_ui_headers(response)

    @pytest.mark.asyncio
    async def test_path_out_of_assets_is_not_cached_for_good(self, client):
        response = await client.get("/ui/assets/..%2findex.html")

        assert response.status_code == 200
        assert response.text == INDEX
        assert response.headers["cache-control"] == "no-cache"

    @pytest.mark.asyncio
    async def test_unhashed_file_is_revalidated(self, client):
        response = await client.get("/ui/theme-init.js")

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"

    @pytest.mark.asyncio
    async def test_missing_file_is_404_not_index(self, client):
        response = await client.get("/ui/assets/missing.js")

        assert response.status_code == 404
        assert response.json() == {"detail": "/ui/assets/missing.js not found"}
        assert_ui_headers(response)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path",
        [
            "/ui/%2e%2e/secret.txt",
            "/ui/..%2fsecret.txt",
            "/ui/assets/..%2f..%2fsecret.txt",
            # A sibling whose name starts with the UI directory's
            "/ui/..%2fdist-other/x.js",
        ],
    )
    async def test_paths_outside_the_ui_dir_are_404(self, client, path):
        response = await client.get(path)

        assert response.status_code == 404
        assert OUTSIDE not in response.text

    @pytest.mark.asyncio
    async def test_overlong_path_is_index_or_404_not_500(self, client):
        # Over NAME_MAX: stat() fails with ENAMETOOLONG, which is_file() re-raises.
        name = "a" * 300

        page = await client.get(f"/ui/{name}")
        asset = await client.get(f"/ui/{name}.js")

        assert page.status_code == 200
        assert page.text == INDEX
        assert asset.status_code == 404

    @pytest.mark.asyncio
    async def test_symlink_out_of_the_ui_dir_is_404(self, client, ui_dir):
        (ui_dir / "leak.txt").symlink_to(ui_dir.parent / "secret.txt")

        response = await client.get("/ui/leak.txt")

        assert response.status_code == 404
        assert OUTSIDE not in response.text


class TestNotBuilt:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/ui/", "/ui/keys", "/ui/assets/app-abc123.js"])
    async def test_404_with_build_hint(self, client, ui_dir, path):
        (ui_dir / "index.html").unlink()

        response = await client.get(path)

        assert response.status_code == 404
        assert response.json() == {"detail": "UI not built: run make ui-build"}

    @pytest.mark.asyncio
    async def test_config_still_answers(self, client, ui_dir):
        (ui_dir / "index.html").unlink()

        assert (await client.get("/ui/config.json")).status_code == 200


class TestConfig:
    @pytest.mark.asyncio
    async def test_returns_supabase_settings(self, client):
        response = await client.get("/ui/config.json")

        assert response.status_code == 200
        assert response.json() == {
            "supabase_url": SUPABASE,
            "anon_key": "anon-key",
            "password_min_length": 8,
            "email": "unknown",
        }
        assert response.headers["cache-control"] == "no-cache"
        assert_ui_headers(response)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("unset", ["supabase_public_url", "anon_key"])
    async def test_503_while_a_setting_is_unset(self, client, unset):
        with patch.object(settings, unset, None):
            response = await client.get("/ui/config.json")

        assert response.status_code == 503
        assert "SUPABASE_PUBLIC_URL and ANON_KEY" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_csp_without_supabase_url_allows_only_self(self, client):
        with patch.object(settings, "supabase_public_url", None):
            response = await client.get("/ui/")

        assert "connect-src 'self';" in response.headers["content-security-policy"]


@pytest.fixture
def smtp():
    """Set the SMTP status preflight found; forget earlier reports."""

    def _set(value: SmtpStatus | None):
        patcher = patch.object(preflight, "_smtp_status", value)
        patcher.start()
        return patcher

    patchers = []
    with patch.dict(ui._last_report, clear=True):
        yield lambda value: patchers.append(_set(value))
    for patcher in patchers:
        patcher.stop()


class TestEmail:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", list(SmtpStatus))
    async def test_config_carries_the_smtp_status(self, client, smtp, value):
        smtp(value)
        response = await client.get("/ui/config.json")
        assert response.json()["email"] == value

    @pytest.mark.asyncio
    async def test_report_logs_once_a_minute(self, client, smtp, caplog):
        smtp(SmtpStatus.not_configured)
        body = {"feature": "password_reset", "reason": "unavailable"}

        first = await client.post("/ui/email-problem", json=body)
        await client.post("/ui/email-problem", json=body)

        assert first.status_code == 204
        assert_ui_headers(first)
        warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert "reset a password" in warnings[0]
        assert "SMTP is not configured (SMTP_HOST is empty)" in warnings[0]

    @pytest.mark.asyncio
    async def test_each_feature_is_logged(self, client, smtp, caplog):
        smtp(SmtpStatus.failed)
        for feature in ("signup", "email_change"):
            await client.post(
                "/ui/email-problem", json={"feature": feature, "reason": "unavailable"}
            )
        warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 2
        assert all("SMTP is not working" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_a_failed_send_is_logged_even_when_smtp_looked_fine(
        self, client, smtp, caplog
    ):
        smtp(SmtpStatus.ok)
        await client.post(
            "/ui/email-problem", json={"feature": "signup", "reason": "send_failed"}
        )
        assert "Supabase Auth could not send the email" in caplog.text

    @pytest.mark.asyncio
    async def test_unavailable_is_ignored_while_smtp_works(self, client, smtp, caplog):
        smtp(SmtpStatus.ok)
        response = await client.post(
            "/ui/email-problem", json={"feature": "signup", "reason": "unavailable"}
        )
        assert response.status_code == 204
        assert "WARNING" not in [r.levelname for r in caplog.records]

    @pytest.mark.asyncio
    async def test_rejects_unknown_reports(self, client, smtp):
        response = await client.post(
            "/ui/email-problem", json={"feature": "anything", "reason": "unavailable"}
        )
        assert response.status_code == 422
