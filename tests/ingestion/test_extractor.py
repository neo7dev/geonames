from __future__ import annotations

import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from geonames_server.ingestion.extractor import Extractor

# ---------------------------------------------------------------------------
# Helper to build an in-memory zip archive
# ---------------------------------------------------------------------------


def _make_zip(zip_path: Path, files: dict[str, bytes]) -> Path:
    """Create a zip at *zip_path* containing the given {name: content} entries."""
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return zip_path


# ---------------------------------------------------------------------------
# Extractor.__init__
# ---------------------------------------------------------------------------


class TestExtractorInit:
    def test_stores_dest_as_path(self, tmp_path):
        ext = Extractor(dest_path=str(tmp_path / "out"))
        assert isinstance(ext.dest_path, Path)

    def test_dest_path_value(self, tmp_path):
        target = tmp_path / "output"
        ext = Extractor(dest_path=target)
        assert ext.dest_path == target


# ---------------------------------------------------------------------------
# Extractor.extract – error cases
# ---------------------------------------------------------------------------


class TestExtractErrors:
    def test_raises_file_not_found_for_missing_zip(self, tmp_path):
        ext = Extractor(dest_path=tmp_path / "out")
        with pytest.raises(FileNotFoundError, match="Zip file not found"):
            ext.extract(tmp_path / "nonexistent.zip")

    def test_raises_on_invalid_zip(self, tmp_path):
        bad_zip = tmp_path / "bad.zip"
        bad_zip.write_bytes(b"not a zip file")

        ext = Extractor(dest_path=tmp_path / "out")
        with pytest.raises(zipfile.BadZipFile):
            ext.extract(bad_zip)


# ---------------------------------------------------------------------------
# Extractor.extract – success cases
# ---------------------------------------------------------------------------


class TestExtract:
    @patch("geonames_server.ingestion.extractor.ExtractorProgressBar")
    def test_extracts_single_file(self, mock_pb_cls, tmp_path):
        mock_pb = MagicMock()
        mock_pb.__enter__ = MagicMock(return_value=mock_pb)
        mock_pb.__exit__ = MagicMock(return_value=False)
        mock_pb_cls.return_value = mock_pb

        content = b"hello world"
        zip_path = _make_zip(tmp_path / "test.zip", {"greeting.txt": content})
        dest = tmp_path / "out"

        Extractor(dest_path=dest).extract(zip_path)

        extracted = dest / "greeting.txt"
        assert extracted.exists()
        assert extracted.read_bytes() == content

    @patch("geonames_server.ingestion.extractor.ExtractorProgressBar")
    def test_extracts_multiple_files(self, mock_pb_cls, tmp_path):
        mock_pb = MagicMock()
        mock_pb.__enter__ = MagicMock(return_value=mock_pb)
        mock_pb.__exit__ = MagicMock(return_value=False)
        mock_pb_cls.return_value = mock_pb

        files = {
            "a.txt": b"aaa",
            "b.txt": b"bbb",
            "c.txt": b"ccc",
        }
        zip_path = _make_zip(tmp_path / "multi.zip", files)
        dest = tmp_path / "out"

        Extractor(dest_path=dest).extract(zip_path)

        for name, data in files.items():
            assert (dest / name).read_bytes() == data

    @patch("geonames_server.ingestion.extractor.ExtractorProgressBar")
    def test_extracts_nested_directories(self, mock_pb_cls, tmp_path):
        mock_pb = MagicMock()
        mock_pb.__enter__ = MagicMock(return_value=mock_pb)
        mock_pb.__exit__ = MagicMock(return_value=False)
        mock_pb_cls.return_value = mock_pb

        files = {"sub/dir/file.txt": b"nested"}
        zip_path = _make_zip(tmp_path / "nested.zip", files)
        dest = tmp_path / "out"

        Extractor(dest_path=dest).extract(zip_path)

        assert (dest / "sub" / "dir" / "file.txt").read_bytes() == b"nested"

    @patch("geonames_server.ingestion.extractor.ExtractorProgressBar")
    def test_creates_dest_directory(self, mock_pb_cls, tmp_path):
        mock_pb = MagicMock()
        mock_pb.__enter__ = MagicMock(return_value=mock_pb)
        mock_pb.__exit__ = MagicMock(return_value=False)
        mock_pb_cls.return_value = mock_pb

        zip_path = _make_zip(tmp_path / "test.zip", {"f.txt": b"x"})
        dest = tmp_path / "new" / "deep" / "out"

        assert not dest.exists()
        Extractor(dest_path=dest).extract(zip_path)
        assert dest.is_dir()
        assert (dest / "f.txt").exists()

    @patch("geonames_server.ingestion.extractor.ExtractorProgressBar")
    def test_progress_bar_receives_total_bytes(self, mock_pb_cls, tmp_path):
        mock_pb = MagicMock()
        mock_pb.__enter__ = MagicMock(return_value=mock_pb)
        mock_pb.__exit__ = MagicMock(return_value=False)
        mock_pb_cls.return_value = mock_pb

        content = b"12345"
        zip_path = _make_zip(tmp_path / "test.zip", {"f.txt": content})
        dest = tmp_path / "out"

        Extractor(dest_path=dest).extract(zip_path)

        mock_pb_cls.assert_called_once()
        call_kwargs = mock_pb_cls.call_args
        assert call_kwargs.kwargs.get("total") == len(content) or call_kwargs[1].get(
            "total"
        ) == len(content)

    @patch("geonames_server.ingestion.extractor.ExtractorProgressBar")
    def test_progress_bar_update_called(self, mock_pb_cls, tmp_path):
        mock_pb = MagicMock()
        mock_pb.__enter__ = MagicMock(return_value=mock_pb)
        mock_pb.__exit__ = MagicMock(return_value=False)
        mock_pb_cls.return_value = mock_pb

        content = b"data"
        zip_path = _make_zip(tmp_path / "test.zip", {"f.txt": content})
        dest = tmp_path / "out"

        Extractor(dest_path=dest).extract(zip_path)

        mock_pb.update.assert_called()
        # Sum of all update() advances should equal the content size
        total_advanced = sum(call.args[0] for call in mock_pb.update.call_args_list)
        assert total_advanced == len(content)

    @patch("geonames_server.ingestion.extractor.ExtractorProgressBar")
    def test_extracts_empty_zip(self, mock_pb_cls, tmp_path):
        mock_pb = MagicMock()
        mock_pb.__enter__ = MagicMock(return_value=mock_pb)
        mock_pb.__exit__ = MagicMock(return_value=False)
        mock_pb_cls.return_value = mock_pb

        zip_path = _make_zip(tmp_path / "empty.zip", {})
        dest = tmp_path / "out"

        Extractor(dest_path=dest).extract(zip_path)
        # Should complete without error; dest dir created
        assert dest.is_dir()

    @patch("geonames_server.ingestion.extractor.ExtractorProgressBar")
    def test_reuse_extractor_for_multiple_zips(self, mock_pb_cls, tmp_path):
        mock_pb = MagicMock()
        mock_pb.__enter__ = MagicMock(return_value=mock_pb)
        mock_pb.__exit__ = MagicMock(return_value=False)
        mock_pb_cls.return_value = mock_pb

        zip1 = _make_zip(tmp_path / "one.zip", {"a.txt": b"aaa"})
        zip2 = _make_zip(tmp_path / "two.zip", {"b.txt": b"bbb"})
        dest = tmp_path / "out"

        ext = Extractor(dest_path=dest)
        ext.extract(zip1)
        ext.extract(zip2)

        assert (dest / "a.txt").read_bytes() == b"aaa"
        assert (dest / "b.txt").read_bytes() == b"bbb"


# ---------------------------------------------------------------------------
# Directory entries, permissions and unsafe paths
# ---------------------------------------------------------------------------


@pytest.fixture
def _no_progress_bar():
    with patch("geonames_server.ingestion.extractor.ExtractorProgressBar"):
        yield


def _entry(name: str, mode: int | None = None) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    if mode is not None:
        info.external_attr = mode << 16
    return info


@pytest.mark.usefixtures("_no_progress_bar")
class TestExtractEntries:
    def test_directory_entries_become_directories(self, tmp_path):
        zip_path = tmp_path / "dirs.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(_entry("empty/"), b"")
            zf.writestr("data/file.txt", b"x")
        dest = tmp_path / "out"

        Extractor(dest_path=dest).extract(zip_path)

        assert (dest / "empty").is_dir()
        assert (dest / "data" / "file.txt").read_bytes() == b"x"

    def test_file_permissions_come_from_the_zip(self, tmp_path):
        zip_path = tmp_path / "perm.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(_entry("script.sh", 0o750), b"#!/bin/sh")
            zf.writestr(_entry("plain.txt"), b"no mode")
        dest = tmp_path / "out"

        Extractor(dest_path=dest).extract(zip_path)

        assert (dest / "script.sh").stat().st_mode & 0o777 == 0o750
        assert (dest / "plain.txt").read_bytes() == b"no mode"

    def test_a_failing_chmod_does_not_fail_the_extraction(self, tmp_path):
        zip_path = tmp_path / "perm.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(_entry("file.txt", 0o640), b"data")
        dest = tmp_path / "out"

        with patch.object(Path, "chmod", side_effect=PermissionError("read-only")):
            Extractor(dest_path=dest).extract(zip_path)

        assert (dest / "file.txt").read_bytes() == b"data"

    @pytest.mark.parametrize(
        "name", ["../escape.txt", "a/../../escape.txt", "/tmp/escape.txt"]
    )
    def test_entries_outside_the_destination_are_refused(self, tmp_path, name):
        zip_path = _make_zip(
            tmp_path / "evil.zip", {"first.txt": b"ok", name: b"pwned"}
        )
        dest = tmp_path / "out"

        with pytest.raises(ValueError, match="Unsafe path"):
            Extractor(dest_path=dest).extract(zip_path)

        assert not (tmp_path / "escape.txt").exists()
        # Checked before anything is written
        assert not (dest / "first.txt").exists()
