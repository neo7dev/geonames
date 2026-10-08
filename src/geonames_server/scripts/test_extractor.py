def test_extrator():
    from pathlib import Path

    from geonames_server.config.config import settings
    from geonames_server.ingestion.extractor import Extractor

    zip_path = Path(settings.data_dir) / "cities500.zip"
    dest_path = Path(settings.data_dir)
    Extractor(dest_path).extract(zip_path)
    extracted_file = dest_path / "cities500.txt"
    assert extracted_file.exists()
    assert extracted_file.is_file()
    print(f"Extracted file: {extracted_file}")


test_extrator()
