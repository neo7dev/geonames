import time
import zipfile
from pathlib import Path

from geonames_server.log.mylogger import get_logger
from geonames_server.rich_console.extractor_progressbar import ExtractorProgressBar

log = get_logger()


class Extractor:
    def __init__(self, dest_path: Path | str) -> None:
        self.dest_path = Path(dest_path)

    def extract(self, zip_path: Path | str) -> None:
        zip_path = Path(zip_path)
        if not zip_path.exists():
            raise FileNotFoundError(f"Zip file not found: {zip_path}")

        self.dest_path.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            # Refuse the whole archive if any entry would land outside dest_path
            dest_root = self.dest_path.resolve()
            for info in zf.infolist():
                if not (dest_root / info.filename).resolve().is_relative_to(dest_root):
                    raise ValueError(
                        f"Unsafe path in {zip_path.name}: {info.filename!r}"
                    )

            # collect file entries (skip directories) and total size
            entries = [info for info in zf.infolist() if not info.is_dir()]
            total_bytes = sum(info.file_size for info in entries)
            started = time.perf_counter()

            with ExtractorProgressBar(
                total=total_bytes, description=f"Extracting {zip_path.name}"
            ) as pb:
                for info in zf.infolist():
                    target_path = self.dest_path / info.filename
                    if info.is_dir():
                        target_path.mkdir(parents=True, exist_ok=True)
                        continue

                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    log.debug(
                        f"Extracting {info.filename} ({info.file_size:,} bytes) "
                        f"to {target_path}"
                    )

                    # stream the file so we can update progress by bytes
                    with (
                        zf.open(info, "r") as source,
                        open(target_path, "wb") as target,
                    ):
                        while True:
                            chunk = source.read(8192)
                            if not chunk:
                                break
                            target.write(chunk)
                            pb.update(len(chunk))

                    # attempt to set file permissions from zip if available
                    try:
                        perm = (info.external_attr >> 16) & 0o777
                        if perm:
                            target_path.chmod(perm)
                    except Exception:
                        # ignore permission setting errors
                        pass

            log.info(
                f"Extracted {zip_path.name}: {len(entries)} file(s), "
                f"{total_bytes / 1_000_000:,.1f} MB in {time.perf_counter() - started:,.0f} s"
            )
