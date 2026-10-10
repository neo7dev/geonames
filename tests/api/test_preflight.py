"""Startup preflight: required services stop the server, SMTP only warns."""

import logging
import smtplib
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from geonames_server.api import preflight
from geonames_server.api.preflight import (
    PreflightError,
    SmtpStatus,
    _Check,
    _with_retries,
    check_smtp,
    run_preflight,
)
from geonames_server.config.config import settings

MODULE = "geonames_server.api.preflight"


@contextmanager
def smtp_settings(**values):
    defaults = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": None,
        "smtp_pass": None,
        "smtp_admin_email": "noreply@example.com",
    }
    defaults.update(values)
    with patch.multiple(settings, **defaults):
        yield


@pytest.fixture
def services():
    """Every required service reachable, SMTP not configured."""
    with (
        patch(f"{MODULE}._check_database", AsyncMock()) as database,
        patch(f"{MODULE}._check_redis", AsyncMock()) as redis,
        patch(f"{MODULE}._check_auth", AsyncMock()) as auth,
        patch(f"{MODULE}.RETRY_SECONDS", 0),
        patch.object(settings, "redis_url", "redis://:secret@redis:6379/0"),
        patch.object(settings, "jwt_secret", SecretStr("x" * 32)),
        patch.object(settings, "smtp_host", None),
        # run_preflight records the SMTP status for /ui/config.json
        patch.object(preflight, "_smtp_status", None),
    ):
        yield {"database": database, "redis": redis, "auth": auth}


class TestRequired:
    @pytest.mark.asyncio
    async def test_passes_when_everything_is_reachable(self, services, caplog):
        with caplog.at_level(logging.INFO):
            await run_preflight()
        assert "Preflight: Database ok" in caplog.text
        # The password in REDIS_URL is never logged.
        assert "Preflight: Redis ok (redis:6379)" in caplog.text
        assert "secret" not in caplog.text
        assert "Preflight: Supabase Auth ok" in caplog.text

    @pytest.mark.asyncio
    async def test_reports_every_failure(self, services):
        services["database"].side_effect = OSError("connection refused")
        services["auth"].side_effect = OSError("name not known")

        with pytest.raises(PreflightError) as failed:
            await run_preflight()

        message = str(failed.value)
        assert "Database at" in message and "connection refused" in message
        assert "Supabase Auth at" in message and "name not known" in message
        assert "Redis" not in message

    @pytest.mark.asyncio
    async def test_redis_is_required_when_configured(self, services):
        services["redis"].side_effect = OSError("timed out")
        with pytest.raises(PreflightError, match="Redis at redis:6379"):
            await run_preflight()

    @pytest.mark.asyncio
    async def test_redis_is_skipped_when_not_configured(self, services, caplog):
        with patch.object(settings, "redis_url", None):
            await run_preflight()
        services["redis"].assert_not_called()
        assert "REDIS_URL is not set" in caplog.text

    @pytest.mark.asyncio
    async def test_missing_jwt_secret_fails(self, services):
        with (
            patch.object(settings, "jwt_secret", None),
            pytest.raises(PreflightError, match="JWT_SECRET"),
        ):
            await run_preflight()

    @pytest.mark.asyncio
    async def test_retries_a_service_still_starting(self):
        run = AsyncMock(side_effect=[OSError("starting"), OSError("starting"), None])
        assert await _with_retries(_Check("Database", "db", run), delay=0) is None
        assert run.await_count == 3

    @pytest.mark.asyncio
    async def test_gives_up_after_the_last_attempt(self):
        run = AsyncMock(side_effect=OSError("down"))
        error = await _with_retries(_Check("Database", "db", run), attempts=2, delay=0)
        assert error == "down"
        assert run.await_count == 2


def _fake_smtp(starttls: bool = True, login_error: Exception | None = None):
    server = MagicMock()
    server.__enter__.return_value = server
    server.has_extn.side_effect = lambda name: starttls and name == "starttls"
    if login_error:
        server.login.side_effect = login_error
    return server


class TestCheckSmtp:
    def test_not_configured(self):
        with smtp_settings(smtp_host=None):
            assert check_smtp() == (SmtpStatus.not_configured, None)

    def test_needs_a_sender_address(self):
        with smtp_settings(smtp_admin_email=None):
            status, error = check_smtp()
        assert status is SmtpStatus.failed
        assert "SMTP_ADMIN_EMAIL" in error

    def test_unreachable_server(self):
        # Nothing listens on port 1.
        with smtp_settings(smtp_host="127.0.0.1", smtp_port=1):
            status, error = check_smtp()
        assert status is SmtpStatus.failed
        assert error.startswith("127.0.0.1:1: ")

    def test_logs_in_over_starttls(self):
        server = _fake_smtp()
        with (
            smtp_settings(smtp_user="mailer", smtp_pass=SecretStr("pw")),
            patch(f"{MODULE}.smtplib.SMTP", return_value=server),
        ):
            assert check_smtp() == (SmtpStatus.ok, None)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("mailer", "pw")
        # Nothing is sent.
        server.sendmail.assert_not_called()
        server.send_message.assert_not_called()

    def test_port_465_uses_tls_from_the_start(self):
        server = _fake_smtp(starttls=False)
        with (
            smtp_settings(smtp_port=465, smtp_user="mailer", smtp_pass=SecretStr("pw")),
            patch(f"{MODULE}.smtplib.SMTP_SSL", return_value=server) as smtp_ssl,
        ):
            assert check_smtp() == (SmtpStatus.ok, None)
        smtp_ssl.assert_called_once()
        server.login.assert_called_once()

    def test_no_password_over_plain_connections(self):
        server = _fake_smtp(starttls=False)
        with (
            smtp_settings(smtp_user="mailer", smtp_pass=SecretStr("pw")),
            patch(f"{MODULE}.smtplib.SMTP", return_value=server),
        ):
            status, error = check_smtp()
        assert status is SmtpStatus.failed
        assert "offers no TLS" in error
        server.login.assert_not_called()

    def test_without_a_user_no_login_is_needed(self):
        # Mailpit in the devcontainer: plain, no authentication.
        server = _fake_smtp(starttls=False)
        with (
            smtp_settings(smtp_host="mailpit", smtp_port=1025),
            patch(f"{MODULE}.smtplib.SMTP", return_value=server),
        ):
            assert check_smtp() == (SmtpStatus.ok, None)

    def test_wrong_password(self):
        server = _fake_smtp(
            login_error=smtplib.SMTPAuthenticationError(535, b"bad credentials")
        )
        with (
            smtp_settings(smtp_user="mailer", smtp_pass=SecretStr("wrong")),
            patch(f"{MODULE}.smtplib.SMTP", return_value=server),
        ):
            status, error = check_smtp()
        assert status is SmtpStatus.failed
        assert "535" in error and "bad credentials" in error


class TestSmtpWarnings:
    @pytest.mark.asyncio
    async def test_not_configured_warns_but_starts(self, services, caplog):
        await run_preflight()
        assert preflight.smtp_status() is SmtpStatus.not_configured
        assert "SMTP is not configured (SMTP_HOST is empty)" in caplog.text
        assert '"Forgot password?"' in caplog.text
        assert "DISABLE_SIGNUP=true" in caplog.text

    @pytest.mark.asyncio
    async def test_broken_config_warns_differently(self, services, caplog):
        with (
            patch.object(settings, "smtp_host", "smtp.example.com"),
            patch(
                f"{MODULE}.check_smtp",
                return_value=(SmtpStatus.failed, "smtp.example.com:587: refused"),
            ),
        ):
            await run_preflight()
        assert (
            "SMTP is configured but not working: smtp.example.com:587: refused"
            in caplog.text
        )
        assert "not configured" not in caplog.text

    @pytest.mark.asyncio
    async def test_no_signup_advice_when_signups_are_off(self, services, caplog):
        with patch.object(settings, "disable_signup", True):
            await run_preflight()
        assert "SMTP is not configured" in caplog.text
        assert "DISABLE_SIGNUP" not in caplog.text

    @pytest.mark.asyncio
    async def test_working_smtp_is_logged(self, services, caplog):
        with (
            caplog.at_level(logging.INFO),
            patch(f"{MODULE}.check_smtp", return_value=(SmtpStatus.ok, None)),
            patch.object(settings, "smtp_host", "smtp.example.com"),
        ):
            await run_preflight()
        assert "Preflight: SMTP ok (smtp.example.com:" in caplog.text
        assert "SMTP is" not in caplog.text


@pytest.mark.asyncio
async def test_web_ui_settings_warn(services, caplog):
    with (
        patch.object(settings, "supabase_public_url", None),
        patch.object(settings, "anon_key", "anon"),
    ):
        await run_preflight()
    assert "SUPABASE_PUBLIC_URL not set: the web UI cannot log anyone in" in caplog.text


def test_module_defaults():
    # A few attempts, a couple of seconds apart: compose may still be starting a service.
    assert preflight.ATTEMPTS * preflight.RETRY_SECONDS >= 5
