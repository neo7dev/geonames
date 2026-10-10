import json
import logging
import logging.config
from pathlib import Path

# -------------------------
# JSON FORMATTER
# -------------------------

LOG_RECORD_BUILTIN_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}

_INITIALIED = False


# -------------------------
# SETUP FUNCTION
# -------------------------


def setup_logging():

    global _INITIALIED

    if _INITIALIED:
        return

    config_path = Path(__file__).parent.parent / "logger_config" / "logger_config.json"

    with open(config_path) as f:
        config = json.load(f)

    logging.config.dictConfig(config)

    _INITIALIED = True


setup_logging()


def set_log_level(level: str) -> None:
    """Apply LOG_LEVEL to the app's logs and the third-party loggers.

    SQLAlchemy logs every statement at INFO, so its engine logger shows them
    only when LOG_LEVEL is DEBUG. Called again once uvicorn has set up its own
    loggers, which resets their levels.
    """
    logging.getLogger().setLevel(level)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if level == "DEBUG" else logging.WARNING
    )
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).setLevel(level)


def get_logger(name: str = None) -> logging.Logger:
    return logging.getLogger(name or "geonames_server")
