from geonames_server.config.config import settings  # noqa: F401
from geonames_server.rich_console.rich_console import (
    debug_variables_table,
    log_variables,  # noqa: F401
    verbose_debug,
)


@verbose_debug
# @log_variables
@debug_variables_table
def test():
    x = 10
    y = {"a": 1}
    temp_dict = {"x": x, "y": y}  # noqa: F841
    return x


test()
