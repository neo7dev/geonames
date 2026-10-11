import sys

import psycopg2

from geonames_server.log.mylogger import get_logger

log = get_logger()


class Connection:
    def __init__(self, user: str, password: str, host: str, port: int, dbname: str):
        self.conn_params = {
            "user": user,
            "password": password,
            "host": host,
            "port": port,
            "dbname": dbname,
        }

    def check(self) -> bool:
        """
        Check if a connection can be established to the database.
        Returns True if successful, False otherwise. Exits app if not.
        """
        try:
            log.debug(
                f"Attempting to connect to the database at {self.conn_params['host']}:{self.conn_params['port']}..."
            )
            with psycopg2.connect(**self.conn_params) as _conn:
                log.info("Database connection successful.")
                return True
        except Exception as e:
            log.error(f"Database connection failed: {e}")
            log.critical("Database must be up and running before the app can start.")
            sys.exit(1)
        return False
