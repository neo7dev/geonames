from rich.traceback import install
from geonames_server.log.mylogger import get_logger
from geonames_server.config.config import settings

install(show_locals=True, extra_lines=3)

log = get_logger()

log.debug("Debug message")

log.info("User logged in", extra={"user": "Alice"})

log.warning("Something suspicious")

log.error("Something failed", extra={"user": "Bob"}, exc_info=True)

log.critical("Critical error occurred")

print(settings.logging_config_file)

try:
    1 / 0
except Exception:
    log.exception("Crash happened", extra={"user": "Admin"}, exc_info=True)
