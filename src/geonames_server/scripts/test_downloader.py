from rich.traceback import install

from geonames_server.config.config import settings
from geonames_server.ingestion.downloader import Downloader

install(show_locals=True, extra_lines=3)


def test_download_file():
    url = settings.geonames_url + "allCountries.zip"
    file_name = "allCountries.zip"
    with Downloader(
        cache_dir=settings.cache_dir, data_dir=settings.data_dir, force=True
    ) as dl:
        downloaded_file = dl.download(url, file_name)
    assert downloaded_file.path.exists()
    assert downloaded_file.path.is_file()
    print(f"Downloaded file: {downloaded_file.path}")


test_download_file()
