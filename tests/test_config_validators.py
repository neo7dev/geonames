from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import make_url

from geonames_server.config.config import Settings
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


class TestPostgresSchema:
    @pytest.mark.parametrize(
        "schema",
        ["public", "auth", "storage", "extensions", "pg_catalog", "supabase_functions"],
    )
    def test_rejects_postgres_and_supabase_schemas(self, schema):
        with pytest.raises(ValidationError, match="Postgres or Supabase schema"):
            Settings(postgres_schema=schema)

    def test_accepts_dedicated_schema(self):
        assert Settings(postgres_schema="geonames").postgres_schema == "geonames"

    @pytest.mark.parametrize(
        "schema", ["GeoNames", "geo names", "1geo", "geo-names", "", "g" * 64]
    )
    def test_rejects_names_that_need_quoting(self, schema):
        with pytest.raises(ValidationError, match="lowercase identifier"):
            Settings(postgres_schema=schema)


class TestJwtSecret:
    @pytest.mark.parametrize("secret", ["", "   "])
    def test_blank_secret_counts_as_not_set(self, secret):
        assert Settings(jwt_secret=secret).jwt_secret is None

    def test_rejects_short_secret(self):
        with pytest.raises(ValidationError, match="at least 32 characters"):
            Settings(jwt_secret="too-short")

    def test_accepts_long_secret(self):
        secret = "s" * 32
        assert Settings(jwt_secret=secret).jwt_secret.get_secret_value() == secret


class TestUiSettings:
    @pytest.mark.parametrize("field", ["supabase_public_url", "anon_key"])
    @pytest.mark.parametrize("value", ["", "   "])
    def test_blank_counts_as_not_set(self, field, value):
        assert getattr(Settings(**{field: value}), field) is None

    @pytest.mark.parametrize(
        "url, expected",
        [
            ("http://localhost:8000", "http://localhost:8000"),
            ("https://auth.example.com/", "https://auth.example.com"),
            ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
            ("http://[::1]:8000", "http://[::1]:8000"),
        ],
    )
    def test_supabase_url_is_an_origin(self, url, expected):
        assert Settings(supabase_public_url=url).supabase_public_url == expected

    @pytest.mark.parametrize(
        "url",
        [
            "localhost:8000",
            "ftp://example.com",
            "https://example.com/auth/v1",
            "https://example.com; script-src *",
            # Valid URLs, but not origins: a CSP source cannot hold them
            "https://user:pw@auth.example.com",
            "https://auth.example.com?x=1",
            "https://auth.example.com#top",
            "https://*",
            "https://*.example.com",
        ],
    )
    def test_rejects_supabase_url_that_is_not_an_origin(self, url):
        with pytest.raises(ValidationError, match="http\\(s\\) origin"):
            Settings(supabase_public_url=url)

    def test_password_min_length_is_at_least_supabase_minimum(self):
        assert Settings().password_min_length == 8
        with pytest.raises(ValidationError):
            Settings(password_min_length=5)

    def test_ui_dir_defaults_to_frontend_build(self, tmp_path):
        assert Settings(root_dir=tmp_path).ui_root == tmp_path / "frontend" / "dist"
        assert Settings(ui_dir=tmp_path / "ui").ui_root == tmp_path / "ui"


class TestPostgresUrl:
    PASSWORD = "p%40ss@w:rd/x"

    def test_password_with_special_characters_round_trips(self):
        url = make_url(Settings(postgres_password=self.PASSWORD).postges_url)

        assert url.password == self.PASSWORD
        assert url.host == Settings().postgres_host

    def test_alembic_config_reads_back_the_same_url(self, monkeypatch):
        from geonames_server.database import migrations

        monkeypatch.setattr(
            migrations, "settings", Settings(postgres_password=self.PASSWORD)
        )

        url = make_url(migrations._alembic_config().get_main_option("sqlalchemy.url"))
        assert url.password == self.PASSWORD
