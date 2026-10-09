from __future__ import annotations

import json
import os
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
        (cache / "test.txt.part.meta").write_text(
            json.dumps({"url": "http://example.com/test.txt", "etag": '"abc"'})
        )

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
        headers = call_kwargs.kwargs["headers"]
        assert headers["Range"] == f"bytes={existing_size}-"
        assert headers["If-Range"] == '"abc"'

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

    @patch("geonames_server.ingestion.downloader.time.sleep")
    @patch("geonames_server.ingestion.downloader.DownloadProgressbar")
    def test_raises_on_request_exception(self, mock_progress_cls, _sleep, tmp_path):
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


# ---------------------------------------------------------------------------
# Resume / retry / verification
# ---------------------------------------------------------------------------

URL = "http://example.com/test.txt"


def _resp(status=200, headers=None, chunks=(), raises=None):
    r = MagicMock()
    r.status_code = status
    r.headers = headers or {}
    if raises:
        r.iter_content.side_effect = raises
    else:
        r.iter_content.return_value = list(chunks)
    r.__enter__ = MagicMock(return_value=r)
    r.__exit__ = MagicMock(return_value=False)
    return r


@pytest.fixture
def dirs(tmp_path):
    cache, data = tmp_path / "cache", tmp_path / "data"
    cache.mkdir()
    data.mkdir()
    return cache, data


@pytest.fixture(autouse=True)
def _no_ui_or_sleep():
    with (
        patch("geonames_server.ingestion.downloader.DownloadProgressbar"),
        patch("geonames_server.ingestion.downloader.time.sleep") as sleep,
    ):
        yield sleep


def _dl(dirs, responses, **kw):
    dl = Downloader(cache_dir=dirs[0], data_dir=dirs[1], **kw)
    dl._session = MagicMock()
    dl._session.get.side_effect = responses
    return dl


def _seed_part(dirs, data=b"partial", etag='"abc"'):
    (dirs[0] / "test.txt.part").write_bytes(data)
    (dirs[0] / "test.txt.part.meta").write_text(json.dumps({"url": URL, "etag": etag}))


class TestAtomicVerified:
    def test_success_removes_part_and_meta(self, dirs):
        dl = _dl(dirs, [_resp(200, {"content-length": "5", "ETag": '"e1"'}, [b"hello"])])
        result = dl.download(URL, "test.txt")
        assert result.etag == '"e1"'
        assert (dirs[1] / "test.txt").read_bytes() == b"hello"
        assert not (dirs[0] / "test.txt.part").exists()
        assert not (dirs[0] / "test.txt.part.meta").exists()

    def test_size_mismatch_never_publishes_dest(self, dirs, _no_ui_or_sleep):
        dl = _dl(dirs, [_resp(200, {"content-length": "10"}, [b"short"])] * 5)
        with pytest.raises(Exception, match="incomplete"):
            dl.download(URL, "test.txt")
        assert not (dirs[1] / "test.txt").exists()

    def test_force_failure_keeps_old_dest(self, dirs):
        (dirs[1] / "test.txt").write_text("old")
        dl = _dl(dirs, [requests.ConnectionError("x")] * 5, force=True)
        with pytest.raises(requests.ConnectionError):
            dl.download(URL, "test.txt")
        assert (dirs[1] / "test.txt").read_text() == "old"


LAST_MODIFIED = "Fri, 09 Oct 2026 02:44:21 GMT"
LAST_MODIFIED_TS = 1791513861  # LAST_MODIFIED as a Unix timestamp


def _head(length="5", last_modified=LAST_MODIFIED, etag='"e1"', status=200):
    r = MagicMock()
    r.status_code = status
    r.headers = {
        k: v
        for k, v in {
            "Content-Length": length,
            "Last-Modified": last_modified,
            "ETag": etag,
        }.items()
        if v is not None
    }
    return r


def _seed_dest(dirs, data=b"hello", mtime=LAST_MODIFIED_TS):
    dest = dirs[1] / "test.txt"
    dest.write_bytes(data)
    os.utime(dest, (mtime, mtime))
    return dest


class TestReusesCurrentCopy:
    def test_download_sets_mtime_to_last_modified(self, dirs):
        headers = {"content-length": "5", "Last-Modified": LAST_MODIFIED}
        dl = _dl(dirs, [_resp(200, headers, [b"hello"])])
        result = dl.download(URL, "test.txt")
        assert int(result.path.stat().st_mtime) == LAST_MODIFIED_TS

    def test_force_reuses_matching_file_without_get(self, dirs):
        dest = _seed_dest(dirs)
        dl = _dl(dirs, [], force=True)
        dl._session.head.return_value = _head()

        result = dl.download(URL, "test.txt")

        dl._session.get.assert_not_called()
        assert result.path == dest
        assert result.etag == '"e1"'
        assert int(result.last_modified.timestamp()) == LAST_MODIFIED_TS
        assert dest.read_bytes() == b"hello"

    def test_force_downloads_when_size_differs(self, dirs):
        _seed_dest(dirs, data=b"old")
        dl = _dl(dirs, [_resp(200, {"content-length": "5"}, [b"hello"])], force=True)
        dl._session.head.return_value = _head(length="5")

        result = dl.download(URL, "test.txt")

        assert result.path.read_bytes() == b"hello"

    def test_force_downloads_when_last_modified_differs(self, dirs):
        _seed_dest(dirs, mtime=LAST_MODIFIED_TS - 86400)
        dl = _dl(dirs, [_resp(200, {"content-length": "5"}, [b"HELLO"])], force=True)
        dl._session.head.return_value = _head()

        result = dl.download(URL, "test.txt")

        assert result.path.read_bytes() == b"HELLO"

    def test_force_downloads_without_last_modified_header(self, dirs):
        _seed_dest(dirs)
        dl = _dl(dirs, [_resp(200, {"content-length": "5"}, [b"HELLO"])], force=True)
        dl._session.head.return_value = _head(last_modified=None)

        result = dl.download(URL, "test.txt")

        assert result.path.read_bytes() == b"HELLO"

    def test_force_downloads_when_head_fails(self, dirs):
        _seed_dest(dirs)
        dl = _dl(dirs, [_resp(200, {"content-length": "5"}, [b"HELLO"])], force=True)
        dl._session.head.side_effect = requests.ConnectionError("x")

        result = dl.download(URL, "test.txt")

        assert result.path.read_bytes() == b"HELLO"

    @pytest.mark.parametrize(
        "head",
        [_head(length="5, 5"), _head(last_modified="yesterday")],
        ids=["bad-content-length", "bad-last-modified"],
    )
    def test_force_downloads_when_head_headers_malformed(self, dirs, head):
        _seed_dest(dirs)
        dl = _dl(dirs, [_resp(200, {"content-length": "5"}, [b"HELLO"])], force=True)
        dl._session.head.return_value = head

        result = dl.download(URL, "test.txt")

        assert result.path.read_bytes() == b"HELLO"

    def test_malformed_get_headers_do_not_fail_download(self, dirs):
        headers = {"content-length": "5, 5", "Last-Modified": "yesterday"}
        dl = _dl(dirs, [_resp(200, headers, [b"hello"])])

        result = dl.download(URL, "test.txt")

        assert result.path.read_bytes() == b"hello"
        assert result.last_modified is None

    def test_force_replaces_unreadable_file(self, dirs):
        dest = _seed_dest(dirs)
        dest.chmod(0o000)
        dl = _dl(dirs, [_resp(200, {"content-length": "5"}, [b"HELLO"])], force=True)

        result = dl.download(URL, "test.txt")

        dl._session.head.assert_not_called()
        assert result.path.read_bytes() == b"HELLO"

    def test_force_downloads_when_head_not_ok(self, dirs):
        _seed_dest(dirs)
        dl = _dl(dirs, [_resp(200, {"content-length": "5"}, [b"HELLO"])], force=True)
        dl._session.head.return_value = _head(status=405)

        result = dl.download(URL, "test.txt")

        assert result.path.read_bytes() == b"HELLO"


class TestResume:
    def test_part_without_validator_restarts(self, dirs):
        (dirs[0] / "test.txt.part").write_bytes(b"junk")
        dl = _dl(dirs, [_resp(200, {"content-length": "2"}, [b"ok"])])
        dl.download(URL, "test.txt")
        assert "Range" not in dl._session.get.call_args.kwargs["headers"]
        assert (dirs[1] / "test.txt").read_bytes() == b"ok"

    def test_200_in_reply_to_range_overwrites(self, dirs):
        _seed_part(dirs)
        dl = _dl(dirs, [_resp(200, {"content-length": "3"}, [b"new"])])
        dl.download(URL, "test.txt")
        assert (dirs[1] / "test.txt").read_bytes() == b"new"

    def test_416_with_complete_part_finalizes(self, dirs):
        _seed_part(dirs, b"complete")
        dl = _dl(dirs, [_resp(416, {"Content-Range": "bytes */8"})])
        result = dl.download(URL, "test.txt")
        assert result.etag == '"abc"'
        assert (dirs[1] / "test.txt").read_bytes() == b"complete"

    def test_416_with_stale_part_restarts(self, dirs):
        _seed_part(dirs, b"stale-and-longer")
        dl = _dl(
            dirs,
            [
                _resp(416, {"Content-Range": "bytes */5"}),
                _resp(200, {"content-length": "5"}, [b"fresh"]),
            ],
        )
        dl.download(URL, "test.txt")
        assert (dirs[1] / "test.txt").read_bytes() == b"fresh"

    def test_206_wrong_offset_restarts(self, dirs):
        _seed_part(dirs)
        dl = _dl(
            dirs,
            [
                _resp(206, {"Content-Range": "bytes 0-4/5"}, [b"xxxxx"]),
                _resp(200, {"content-length": "5"}, [b"clean"]),
            ],
        )
        dl.download(URL, "test.txt")
        assert (dirs[1] / "test.txt").read_bytes() == b"clean"

    def test_midstream_failure_resumes_from_part(self, dirs):
        dl = _dl(
            dirs,
            [
                _resp(
                    200,
                    {"content-length": "10", "ETag": '"v1"'},
                    raises=requests.exceptions.ChunkedEncodingError("cut"),
                ),
                _resp(206, {"Content-Range": "bytes 0-9/10"}, [b"0123456789"]),
            ],
        )
        # first response writes nothing; second sees empty part and no Range
        dl.download(URL, "test.txt")
        assert (dirs[1] / "test.txt").read_bytes() == b"0123456789"


class TestRetries:
    def test_retries_5xx_then_succeeds(self, dirs, _no_ui_or_sleep):
        dl = _dl(
            dirs,
            [
                _resp(503, {"Retry-After": "7"}),
                _resp(200, {"content-length": "2"}, [b"ok"]),
            ],
        )
        dl.download(URL, "test.txt")
        assert dl._session.get.call_count == 2
        assert _no_ui_or_sleep.call_args.args[0] >= 7

    def test_gives_up_after_max_attempts(self, dirs):
        dl = _dl(dirs, [_resp(500)] * 3, max_attempts=3)
        with pytest.raises(Exception, match="HTTP 500"):
            dl.download(URL, "test.txt")
        assert dl._session.get.call_count == 3

    def test_4xx_not_retried(self, dirs):
        r = _resp(404)
        r.raise_for_status.side_effect = requests.HTTPError("404")
        dl = _dl(dirs, [r])
        with pytest.raises(requests.HTTPError):
            dl.download(URL, "test.txt")
        assert dl._session.get.call_count == 1

    def test_timeout_passed_on_every_request(self, dirs):
        dl = _dl(dirs, [_resp(200, {"content-length": "2"}, [b"ok"])])
        dl.download(URL, "test.txt")
        assert dl._session.get.call_args.kwargs["timeout"] == (10, 60)

    def test_keyboard_interrupt_propagates_and_keeps_part(self, dirs):
        dl = _dl(
            dirs,
            [_resp(200, {"content-length": "9", "ETag": '"v"'}, raises=KeyboardInterrupt())],
        )
        with pytest.raises(KeyboardInterrupt):
            dl.download(URL, "test.txt")
        assert not (dirs[1] / "test.txt").exists()
        assert (dirs[0] / "test.txt.part.meta").exists()
