from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
import requests

from geonames_server.ingestion.remote import RemoteFileClient

UTC = ZoneInfo("UTC")


class TestRemoteFileClient:
    def test_head_parses_headers(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "Content-Length": "1048576",
            "Last-Modified": "Sun, 15 Mar 2026 12:00:00 GMT",
            "ETag": '"abc123"',
            "Content-Type": "application/zip",
            "Accept-Ranges": "bytes",
        }
        with patch("geonames_server.ingestion.remote.requests.Session") as MockSession:
            MockSession.return_value.head.return_value = mock_resp
            with RemoteFileClient() as client:
                meta = client.head("https://example.com/file.zip")
        assert meta.content_length == 1048576
        assert meta.etag == '"abc123"'
        assert meta.supports_resume is True

    @pytest.mark.parametrize("status", [404, 410])
    def test_exists_returns_false_when_missing(self, status):
        mock_resp = MagicMock()
        mock_resp.status_code = status
        with patch("geonames_server.ingestion.remote.requests.Session") as MockSession:
            MockSession.return_value.head.return_value = mock_resp
            with RemoteFileClient() as client:
                assert client.exists("https://example.com/missing.zip") is False

    def test_exists_returns_true_on_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("geonames_server.ingestion.remote.requests.Session") as MockSession:
            MockSession.return_value.head.return_value = mock_resp
            with RemoteFileClient() as client:
                assert client.exists("https://example.com/file.zip") is True

    def test_exists_raises_on_server_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.raise_for_status.side_effect = requests.HTTPError("503")
        with patch("geonames_server.ingestion.remote.requests.Session") as MockSession:
            MockSession.return_value.head.return_value = mock_resp
            with RemoteFileClient() as client:
                with pytest.raises(requests.HTTPError):
                    client.exists("https://example.com/file.zip")

    def test_exists_raises_on_network_error(self):
        with patch("geonames_server.ingestion.remote.requests.Session") as MockSession:
            MockSession.return_value.head.side_effect = requests.RequestException(
                "timeout"
            )
            with RemoteFileClient() as client:
                with pytest.raises(requests.RequestException):
                    client.exists("https://example.com/file.zip")

    def test_has_changed_returns_true_when_no_baseline(self):
        with patch("geonames_server.ingestion.remote.requests.Session"):
            with RemoteFileClient() as client:
                assert client.has_changed("https://example.com/file.zip") is True

    def test_has_changed_returns_false_on_304_with_etag(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 304
        with patch("geonames_server.ingestion.remote.requests.Session") as MockSession:
            MockSession.return_value.head.return_value = mock_resp
            with RemoteFileClient() as client:
                result = client.has_changed(
                    "https://example.com/file.zip", known_etag='"abc123"'
                )
            MockSession.return_value.head.assert_called_once()
            call_kwargs = MockSession.return_value.head.call_args
            assert call_kwargs.kwargs["headers"]["If-None-Match"] == '"abc123"'
            assert result is False

    def test_has_changed_returns_true_on_200_with_etag(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("geonames_server.ingestion.remote.requests.Session") as MockSession:
            MockSession.return_value.head.return_value = mock_resp
            with RemoteFileClient() as client:
                result = client.has_changed(
                    "https://example.com/file.zip", known_etag='"abc123"'
                )
            assert result is True

    def test_has_changed_returns_false_on_304_with_last_modified(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 304
        with patch("geonames_server.ingestion.remote.requests.Session") as MockSession:
            MockSession.return_value.head.return_value = mock_resp
            with RemoteFileClient() as client:
                dt = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
                result = client.has_changed(
                    "https://example.com/file.zip", known_last_modified=dt
                )
            call_kwargs = MockSession.return_value.head.call_args
            assert "If-Modified-Since" in call_kwargs.kwargs["headers"]
            assert result is False

    def test_has_changed_sends_both_conditional_headers(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 304
        with patch("geonames_server.ingestion.remote.requests.Session") as MockSession:
            MockSession.return_value.head.return_value = mock_resp
            with RemoteFileClient() as client:
                dt = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
                result = client.has_changed(
                    "https://example.com/file.zip",
                    known_etag='"abc123"',
                    known_last_modified=dt,
                )
            call_kwargs = MockSession.return_value.head.call_args
            assert call_kwargs.kwargs["headers"]["If-None-Match"] == '"abc123"'
            assert "If-Modified-Since" in call_kwargs.kwargs["headers"]
            assert result is False
