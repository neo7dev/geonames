from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from geonames_server.ingestion.downloader import (
    DownloadResult,
    Downloader,
    _create_directory,
    _has_read_access,
    _has_write_access,
    _human_readable_bytes,
)

# ---------------------------------------------------------------------------
# Helper-function tests
# ---------------------------------------------------------------------------


class TestHumanReadableBytes:
    def test_none_returns_unknown(self):
        assert _human_readable_bytes(None) == "unknown"

    def test_zero_returns_unknown(self):
        assert _human_readable_bytes(0) == "unknown"

    def test_bytes(self):
        assert _human_readable_bytes(512) == "512.00 B"

    def test_kilobytes(self):
        assert _human_readable_bytes(2048) == "2.00 KB"

    def test_megabytes(self):
        assert _human_readable_bytes(5 * 1024 * 1024) == "5.00 MB"

    def test_gigabytes(self):
        assert _human_readable_bytes(3 * 1024**3) == "3.00 GB"

    def test_terabytes(self):
        assert _human_readable_bytes(2 * 1024**4) == "2.00 TB"

    def test_petabytes(self):
        assert _human_readable_bytes(1024**5) == "1.00 PB"


class TestCreateDirectory:
    def test_creates_new_directory(self, tmp_path):
        target = tmp_path / "sub" / "dir"
        result = _create_directory(target)
        assert result == target
        assert target.is_dir()

    def test_existing_directory_returns_path(self, tmp_path):
        result = _create_directory(tmp_path)
        assert result == tmp_path

    def test_raises_if_path_is_file(self, tmp_path):
        file_path = tmp_path / "afile"
        file_path.touch()
        with pytest.raises(NotADirectoryError):
            _create_directory(file_path)


class TestHasReadAccess:
    def test_readable_file(self, tmp_path):
        f = tmp_path / "readable.txt"
        f.write_text("data")
        assert _has_read_access(f) is True

    def test_nonexistent_file(self, tmp_path):
        assert _has_read_access(tmp_path / "nope.txt") is False


class TestHasWriteAccess:
    def test_writable_file(self, tmp_path):
        f = tmp_path / "writable.txt"
        f.write_text("data")
        assert _has_write_access(f) is True

    def test_nonexistent_file_in_writable_dir(self, tmp_path):
        assert _has_write_access(tmp_path / "new.txt") is True


# ---------------------------------------------------------------------------
# Downloader class tests
# ---------------------------------------------------------------------------


class TestDownloaderInit:
    def test_stores_paths_as_path_objects(self, tmp_path):
        dl = Downloader(
            cache_dir=str(tmp_path / "cache"), data_dir=str(tmp_path / "data")
        )
        assert isinstance(dl.cache_dir, Path)
        assert isinstance(dl.data_dir, Path)
        dl.close()

    def test_default_force_is_false(self, tmp_path):
        dl = Downloader(cache_dir=tmp_path, data_dir=tmp_path)
        assert dl.force is False
        dl.close()

    def test_context_manager_closes_session(self, tmp_path):
        with Downloader(cache_dir=tmp_path, data_dir=tmp_path) as dl:
            dl._session = MagicMock()
            mock_session = dl._session
        mock_session.close.assert_called_once()


class TestDownloaderSkipsExisting:
    def test_returns_existing_file_when_not_forced(self, tmp_path):
        cache = tmp_path / "cache"
        data = tmp_path / "data"
        cache.mkdir()
        data.mkdir()

        dest = data / "test.txt"
        dest.write_text("already here")

        with Downloader(cache_dir=cache, data_dir=data, force=False) as dl:
            result = dl.download("http://example.com/test.txt", "test.txt")

        assert isinstance(result, DownloadResult)
        assert result.path == dest

    def test_raises_permission_error_for_unreadable_existing(self, tmp_path):
        cache = tmp_path / "cache"
        data = tmp_path / "data"
        cache.mkdir()
        data.mkdir()

        dest = data / "test.txt"
        dest.write_text("data")
        dest.chmod(0o000)

        try:
            with Downloader(cache_dir=cache, data_dir=data, force=False) as dl:
                with pytest.raises(PermissionError, match="No read access"):
                    dl.download("http://example.com/test.txt", "test.txt")
        finally:
            dest.chmod(0o644)


class TestDownloaderDownload:
    @patch("geonames_server.ingestion.downloader.DownloadProgressbar")
    def test_fresh_download(self, mock_progress_cls, tmp_path):
        cache = tmp_path / "cache"
        data = tmp_path / "data"

        content = b"hello world"

        # Mock the progress bar
        mock_progress = MagicMock()
        mock_progress.__enter__ = MagicMock(return_value=mock_progress)
        mock_progress.__exit__ = MagicMock(return_value=False)
        mock_progress_cls.return_value = mock_progress

        # Build a mock response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-length": str(len(content))}
        mock_resp.iter_content.return_value = [content]
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with Downloader(cache_dir=cache, data_dir=data) as dl:
            dl._session = MagicMock()
            dl._session.get.return_value = mock_resp

            result = dl.download("http://example.com/test.txt", "test.txt")

        assert isinstance(result, DownloadResult)
        assert result.path == data / "test.txt"
        assert result.path.read_bytes() == content

    @patch("geonames_server.ingestion.downloader.DownloadProgressbar")
    def test_resumed_download(self, mock_progress_cls, tmp_path):
        cache = tmp_path / "cache"
        data = tmp_path / "data"
        cache.mkdir()
        data.mkdir()

        # Simulate partial download
        part_file = cache / "test.txt.part"
        part_file.write_bytes(b"partial")
        existing_size = part_file.stat().st_size

        remaining = b" data"

        mock_progress = MagicMock()
        mock_progress.__enter__ = MagicMock(return_value=mock_progress)
        mock_progress.__exit__ = MagicMock(return_value=False)
        mock_progress_cls.return_value = mock_progress

        mock_resp = MagicMock()
        mock_resp.status_code = 206
        mock_resp.headers = {
            "Content-Range": f"bytes {existing_size}-11/12",
        }
        mock_resp.iter_content.return_value = [remaining]
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with Downloader(cache_dir=cache, data_dir=data) as dl:
            dl._session = MagicMock()
            dl._session.get.return_value = mock_resp

            result = dl.download("http://example.com/test.txt", "test.txt")

        assert isinstance(result, DownloadResult)
        assert result.path == data / "test.txt"
        assert result.path.read_bytes() == b"partial data"

        # Verify Range header was sent
        call_kwargs = dl._session.get.call_args
        assert f"bytes={existing_size}-" in call_kwargs.kwargs.get("headers", {}).get(
            "Range", ""
        )

    @patch("geonames_server.ingestion.downloader.DownloadProgressbar")
    def test_force_redownloads_existing_file(self, mock_progress_cls, tmp_path):
        cache = tmp_path / "cache"
        data = tmp_path / "data"
        cache.mkdir()
        data.mkdir()

        dest = data / "test.txt"
        dest.write_text("old content")

        new_content = b"new content"

        mock_progress = MagicMock()
        mock_progress.__enter__ = MagicMock(return_value=mock_progress)
        mock_progress.__exit__ = MagicMock(return_value=False)
        mock_progress_cls.return_value = mock_progress

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-length": str(len(new_content))}
        mock_resp.iter_content.return_value = [new_content]
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with Downloader(cache_dir=cache, data_dir=data, force=True) as dl:
            dl._session = MagicMock()
            dl._session.get.return_value = mock_resp

            result = dl.download("http://example.com/test.txt", "test.txt")

        assert isinstance(result, DownloadResult)
        assert result.path == dest
        assert result.path.read_bytes() == new_content

    @patch("geonames_server.ingestion.downloader.DownloadProgressbar")
    def test_raises_on_http_error(self, mock_progress_cls, tmp_path):
        cache = tmp_path / "cache"
        data = tmp_path / "data"

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with Downloader(cache_dir=cache, data_dir=data) as dl:
            dl._session = MagicMock()
            dl._session.get.return_value = mock_resp

            with pytest.raises(requests.HTTPError):
                dl.download("http://example.com/missing.txt", "missing.txt")

    @patch("geonames_server.ingestion.downloader.DownloadProgressbar")
    def test_raises_on_request_exception(self, mock_progress_cls, tmp_path):
        cache = tmp_path / "cache"
        data = tmp_path / "data"
        cache.mkdir()
        data.mkdir()

        with Downloader(cache_dir=cache, data_dir=data) as dl:
            dl._session = MagicMock()
            dl._session.get.side_effect = requests.ConnectionError("refused")

            with pytest.raises(requests.ConnectionError):
                dl.download("http://example.com/fail.txt", "fail.txt")

    def test_creates_directories_if_missing(self, tmp_path):
        cache = tmp_path / "new_cache"
        data = tmp_path / "new_data"

        dest = data / "test.txt"

        with Downloader(cache_dir=cache, data_dir=data, force=False) as dl:
            # Put the file in place so it returns early without network call
            cache.mkdir(parents=True)
            data.mkdir(parents=True)
            dest.write_text("exists")

            result = dl.download("http://example.com/test.txt", "test.txt")

        assert isinstance(result, DownloadResult)
        assert result.path == dest
