"""Console logs: drawn at LOG_WIDTH without a terminal, quiet requests, progress in steps."""

import io
import logging

import pytest
from rich.console import Console
from rich.logging import RichHandler

from geonames_server.config.config import Settings
from geonames_server.log import console as console_module
from geonames_server.log.console import ProgressMilestones, set_log_width
from geonames_server.log.filters import AccessLogFilter, AlembicNoiseFilter
from geonames_server.log.formatters import RichExtraFormatter


class TestLogWidth:
    def test_defaults_to_160(self, monkeypatch):
        monkeypatch.delenv("LOG_WIDTH", raising=False)
        assert Settings(_env_file=None).log_width == 160

    def test_without_a_terminal_lines_use_the_width(self, monkeypatch):
        out = io.StringIO()
        fake = Console(file=out)  # not a terminal
        monkeypatch.setattr(console_module, "console", fake)

        set_log_width(120)

        assert fake.width == 120

    def test_forced_colour_still_uses_the_width(self, monkeypatch):
        """FORCE_COLOR makes Rich call any output a terminal; it still is not one."""
        fake = Console(file=io.StringIO(), force_terminal=True)
        monkeypatch.setattr(console_module, "console", fake)

        set_log_width(120)

        assert fake.width == 120

    def test_a_terminal_keeps_its_own_width(self, monkeypatch):
        class Tty(io.StringIO):
            def isatty(self):
                return True

        fake = Console(file=Tty(), width=90)
        monkeypatch.setattr(console_module, "console", fake)

        set_log_width(120)

        assert fake.width == 90


def _access(method: str, path: str, status: int = 200) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("172.18.0.1:5000", method, path, "1.1", status),
        None,
    )


class TestAccessLog:
    @pytest.fixture(autouse=True)
    def info(self):
        logger = logging.getLogger("uvicorn.access")
        before = logger.level
        logger.setLevel(logging.INFO)
        yield logger
        logger.setLevel(before)

    def test_one_aligned_line_per_request(self):
        record = _access("GET", "/cities15000/?limit=5", 404)

        assert AccessLogFilter().filter(record)
        assert record.getMessage() == (
            "GET     [yellow]404[/]  /cities15000/?limit=5  [dim]172.18.0.1:5000[/dim]"
        )
        assert record.markup is True

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/ui/assets/index-abc.js"),
            ("GET", "/ui/favicon.svg"),
            ("GET", "/health"),
            ("OPTIONS", "/portal/me"),
        ],
    )
    def test_static_files_preflights_and_health_checks_are_debug(
        self, info, method, path
    ):
        assert not AccessLogFilter().filter(_access(method, path))

        info.setLevel(logging.DEBUG)
        record = _access(method, path)
        assert AccessLogFilter().filter(record)
        assert record.levelno == logging.DEBUG

    def test_paths_cannot_inject_markup(self):
        record = _access("GET", "/x?[bold]y[/bold]")
        AccessLogFilter().filter(record)

        out = io.StringIO()
        handler = RichHandler(console=Console(file=out, width=200))
        handler.setFormatter(RichExtraFormatter())
        handler.emit(record)

        assert "/x?[bold]y[/bold]" in out.getvalue()


def test_uvicorns_colour_copy_is_not_repeated():
    record = logging.LogRecord(
        "uvicorn.error", logging.INFO, __file__, 1, "Hi", (), None
    )
    record.color_message = "Hi in colour"

    assert RichExtraFormatter().format(record) == "Hi"


class TestProgressMilestones:
    def test_logs_every_ten_percent(self, caplog):
        steps = ProgressMilestones(
            logging.getLogger("test"), "Downloading x.zip", 100_000_000
        )
        with caplog.at_level(logging.INFO, logger="test"):
            for _ in range(25):
                steps.advance(1_000_000)

        assert [r.getMessage() for r in caplog.records] == [
            "Downloading x.zip: 10% (10.0 of 100.0 MB)",
            "Downloading x.zip: 20% (20.0 of 100.0 MB)",
        ]

    def test_a_resumed_download_starts_from_where_it_was(self, caplog):
        steps = ProgressMilestones(
            logging.getLogger("test"), "Downloading x.zip", 100, 55, unit="B"
        )
        with caplog.at_level(logging.INFO, logger="test"):
            steps.set(59)
            steps.set(61)

        assert [r.getMessage() for r in caplog.records] == [
            "Downloading x.zip: 60% (61.0 of 100.0 B)"
        ]

    def test_unknown_total_logs_nothing(self, caplog):
        steps = ProgressMilestones(logging.getLogger("test"), "x", None)
        with caplog.at_level(logging.INFO, logger="test"):
            steps.advance(10**9)

        assert caplog.records == []


@pytest.mark.parametrize(
    ("message", "shown"),
    [
        ("Context impl PostgresqlImpl.", False),
        ("Will assume transactional DDL.", False),
        ("Running upgrade %s -> %s, %s", True),
    ],
)
def test_alembics_setup_lines_are_debug(message, shown):
    logger = logging.getLogger("alembic.runtime.migration")
    before = logger.level
    logger.setLevel(logging.INFO)
    try:
        record = logger.makeRecord(
            logger.name, logging.INFO, __file__, 1, message, (), None
        )
        assert AlembicNoiseFilter().filter(record) is shown
    finally:
        logger.setLevel(before)
