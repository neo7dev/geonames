import psycopg2

from geonames_server.config.config import settings
from geonames_server.database.models.allcountries import AllCountries
from geonames_server.ingestion.ingestor import Ingestor


def _conn_params():
    return {
        "user": settings.postgres_user,
        "password": settings.postgres_password,
        "host": settings.postgres_host,
        "port": settings.postgres_port,
        "dbname": settings.postgres_db,
    }


def run_ingest_cities15000():
    params = _conn_params()

    # use lowercase table name so unquoted SQL identifiers match
    table_name = "allcountries"
    data_file = settings.data_dir / "allCountries.txt"

    if not data_file.exists():
        print(
            f"Data file not found: {data_file}. Please download and place it in the data directory."
        )
        return

    # create table if not exists with compatible schema
    create_sql = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        geonameid BIGINT PRIMARY KEY,
        name TEXT NOT NULL,
        asciiname TEXT NOT NULL,
        alternatenames TEXT,
        latitude DOUBLE PRECISION NOT NULL,
        longitude DOUBLE PRECISION NOT NULL,
        feature_class TEXT ,
        feature_code TEXT,
        country_code TEXT,
        cc2 TEXT,
        admin1_code TEXT,
        admin2_code TEXT,
        admin3_code TEXT,
        admin4_code TEXT,
        population BIGINT,
        elevation INTEGER,
        dem INTEGER,
        timezone TEXT,
        modification_date TEXT
    )
    """

    try:
        with psycopg2.connect(**params) as conn:
            with conn.cursor() as cur:
                cur.execute(create_sql)
                conn.commit()

        print(f"Starting ingestion of {data_file} into table {table_name}...")
        Ingestor().ingest(settings.data_dir / "allCountries.txt", AllCountries)

        with psycopg2.connect(**params) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {table_name}")
                count = cur.fetchone()[0]
                print(f"Rows in {table_name}: {count}")

    except Exception as e:
        print("Ingestion failed:", e)


if __name__ == "__main__":
    run_ingest_cities15000()
