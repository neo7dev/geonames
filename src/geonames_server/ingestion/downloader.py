from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests

from geonames_server.log.mylogger import get_logger
from geonames_server.rich_console.download_progressbar import DownloadProgressbar
from geonames_server.utils.utils import rfc2822_to_datetime

log = get_logger()


@dataclass
class DownloadResult:
    """Result of a download operation with remote metadata."""

    path: Path
    etag: str | None = None
    last_modified: datetime | None = None


CHUNK_SIZE = 1024 * 1024  # 1MB chunks


def _create_directory(path: Path | str) -> Path:
    p = Path(path)

    if p.exists():
        if not p.is_dir():
            raise NotADirectoryError(f"Path exists but is not directory: {p}")
        return p

    p.mkdir(parents=True, exist_ok=True)
    log.info(f"Directory created: {p}")
    return p


def _has_read_access(path: Path | str) -> bool:
    p = Path(path)
    return os.access(p, os.R_OK)


def _has_write_access(path: Path | str) -> bool:
    p = Path(path)

    if p.exists():
        return os.access(p, os.W_OK)

    return os.access(p.parent, os.W_OK)


def _human_readable_bytes(num: int | None) -> str:
    if not num:
        return "unknown"

    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num)

    for unit in units:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024

    return f"{size:.2f} PB"


class Downloader:
    def __init__(
        self,
        cache_dir: Path | str,
        data_dir: Path | str,
        force: bool = False,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.data_dir = Path(data_dir)
        self.force = force
        self._session = requests.Session()

    def __enter__(self) -> Downloader:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._session.close()

    def download(self, url: str, file_name: str) -> DownloadResult:
        _create_directory(self.cache_dir)
        _create_directory(self.data_dir)

        dest = self.data_dir / file_name
        part = self.cache_dir / f"{file_name}.part"
        cache_final = self.cache_dir / file_name

        if dest.exists() and not self.force:
            if not _has_read_access(dest):
                raise PermissionError(f"No read access to {dest}")
            return DownloadResult(path=dest)

        if not _has_write_access(part):
            raise PermissionError(f"No write access to {part}")

        if not _has_write_access(dest):
            raise PermissionError(f"No write access to {dest}")

        existing_size = part.stat().st_size if part.exists() else 0

        headers: dict[str, str] = {"Accept-Encoding": "identity"}

        if existing_size > 0:
            headers["Range"] = f"bytes={existing_size}-"

        try:
            with self._session.get(url, stream=True, headers=headers) as resp:
                if resp.status_code not in (200, 206):
                    resp.raise_for_status()

                # determine total size
                total_bytes = None

                if resp.status_code == 206:
                    content_range = resp.headers.get("Content-Range")

                    if content_range and "/" in content_range:
                        total_bytes = int(content_range.split("/")[-1])

                else:
                    content_length = resp.headers.get("content-length")

                    if content_length:
                        total_bytes = int(content_length)

                        if existing_size:
                            total_bytes += existing_size

                log.info(
                    f"Starting download: {url} -> {dest} "
                    f"(size: {_human_readable_bytes(total_bytes)} "
                    f"{'' if existing_size == 0 else f', resumed_at: {_human_readable_bytes(existing_size)}'})"
                )

                start = time.perf_counter()
                downloaded = 0

                mode = "ab" if existing_size else "wb"

                with DownloadProgressbar() as progress:
                    progress.add(
                        f"Downloading {file_name}",
                        total=total_bytes,
                        completed=existing_size,
                    )

                    with part.open(mode, buffering=1024 * 1024) as fh:
                        for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                            if not chunk:
                                continue

                            fh.write(chunk)

                            size = len(chunk)
                            downloaded += size

                            progress.update(advance=size)

                elapsed = time.perf_counter() - start
                total_downloaded = existing_size + downloaded

                avg_speed = total_downloaded / elapsed if elapsed > 0 else 0

                log.info(
                    f"Finished download: {dest} "
                    f"({_human_readable_bytes(total_downloaded)}) "
                    f"avg speed: {_human_readable_bytes(int(avg_speed))}/s"
                )

        except requests.RequestException as e:
            log.exception(f"Download failed: {url}")
            raise e

        except KeyboardInterrupt:
            log.warning("Download interrupted by user")
            return DownloadResult(path=dest)

        # Parse remote metadata from response headers
        etag = resp.headers.get("ETag")
        raw_lm = resp.headers.get("Last-Modified")
        last_modified = rfc2822_to_datetime(raw_lm) if raw_lm else None

        if part.exists():
            part.rename(cache_final)
            shutil.move(str(cache_final), str(dest))

        return DownloadResult(path=dest, etag=etag, last_modified=last_modified)
