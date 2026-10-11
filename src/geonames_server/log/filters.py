import logging

from rich.markup import escape


class OnlyErrorsFilter(logging.Filter):
    def filter(self, record):

        return record.levelno >= logging.ERROR


# Requests nobody needs to see at INFO: the web UI's files, CORS preflights, health checks
_QUIET_PREFIXES = ("/ui/assets/", "/health")
_QUIET_FILES = ("/ui/favicon.svg", "/ui/config.json", "/favicon.ico")


def _demote(record: logging.LogRecord) -> bool:
    """Make the record DEBUG; whether it is still logged at the logger's level."""
    record.levelno, record.levelname = logging.DEBUG, "DEBUG"
    return logging.getLogger(record.name).isEnabledFor(logging.DEBUG)


class AlembicNoiseFilter(logging.Filter):
    """Alembic's per-run setup lines are DEBUG; "Running upgrade ..." stays INFO."""

    _NOISE = ("Context impl ", "Will assume ")

    def filter(self, record: logging.LogRecord) -> bool:
        if str(record.msg).startswith(self._NOISE):
            return _demote(record)
        return True


class AccessLogFilter(logging.Filter):
    """One aligned line per request: method, status, path, client.

    Requests for static files, CORS preflights and health checks are DEBUG,
    so they show only with LOG_LEVEL=DEBUG.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not isinstance(record.args, tuple) or len(record.args) != 5:
            return True
        client, method, path, _http_version, status = record.args
        if (
            method == "OPTIONS"
            or path.startswith(_QUIET_PREFIXES)
            or path.split("?", 1)[0] in _QUIET_FILES
        ):
            if not _demote(record):
                return False
        colour = "green" if status < 400 else "yellow" if status < 500 else "red"
        record.msg = "%-7s [%s]%s[/]  %s  [dim]%s[/dim]"
        record.args = (method, colour, status, escape(path), escape(str(client)))
        record.markup = True
        # uvicorn's coloured copy of the message; Rich colours it itself
        record.__dict__.pop("color_message", None)
        return True
