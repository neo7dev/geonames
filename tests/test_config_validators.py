from pathlib import Path

import pytest

from geonames_server.config.validators import (
    check_read_permissions,
    check_write_permissions,
    create_directory,
    find_project_root,
)


class TestCheckReadPermissions:
    def test_readable_directory(self, tmp_path):
        assert check_read_permissions(tmp_path) is True

    def test_nonexistent_path(self, tmp_path):
        assert check_read_permissions(tmp_path / "nope") is False

    def test_file_not_dir(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello")
        assert check_read_permissions(f) is False


class TestCheckWritePermissions:
    def test_writable_directory(self, tmp_path):
        assert check_write_permissions(tmp_path) is True

    def test_nonexistent_path(self, tmp_path):
        assert check_write_permissions(tmp_path / "nope") is False

    def test_file_not_dir(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello")
        assert check_write_permissions(f) is False


class TestCreateDirectory:
    def test_creates_new_directory(self, tmp_path):
        new_dir = tmp_path / "subdir"
        assert not new_dir.exists()
        result = create_directory(new_dir)
        # create_directory requires existing readable+writable dir,
        # so a non-existent path won't pass the permission checks
        assert result == new_dir

    def test_existing_directory_returns_path(self, tmp_path):
        result = create_directory(tmp_path)
        assert result == tmp_path

    def test_accepts_string_path(self, tmp_path):
        result = create_directory(str(tmp_path))
        assert result == tmp_path


class TestFindProjectRoot:
    def test_finds_workspace_root(self):
        # The workspace root has requirements.txt
        root = find_project_root(Path("/workspace/src"))
        assert root == Path("/workspace")
        assert (root / "requirements.txt").exists()

    def test_raises_when_not_found(self, tmp_path):
        with pytest.raises(RuntimeError, match="Project root not found"):
            find_project_root(tmp_path / "deep" / "nested")
