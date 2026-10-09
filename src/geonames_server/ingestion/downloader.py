from __future__ import annotations

import errno
import json
import os
import random
import re
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

CHUNK_SIZE = 1024 * 1024  # 1MB chunks
TIMEOUT = (10, 60)  # connect, read (seconds)
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_BACKOFF = 60.0

_CONTENT_RANGE = re.compile(r"bytes (\d+)-\d+/(\d+|\*)")
_UNSATISFIED_RANGE = re.compile(r"bytes \*/(\d+)")


@dataclass
class DownloadResult:
    """Result of a download operation with remote metadata."""

    path: Path
    etag: str | None = None
    last_modified: datetime | None = None


class _Transient(Exception):
    """Retryable failure (5xx/429, stale partial, short body)."""

    def __init__(self, msg: str, retry_after: float | None = None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after


_RETRYABLE = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
    _Transient,
)


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


def _unlink(path: Path) -> None:
    path.unlink(missing_ok=True)


def _atomic_move(src: Path, dest: Path) -> None:
    """Atomically replace *dest* with *src*, copying first across filesystems."""
    try:
        os.replace(src, dest)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        tmp = dest.with_name(dest.name + ".tmp")
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
        _unlink(src)


class Downloader:
    """Resumable, verified HTTP downloader.

    Streams to ``<cache_dir>/<file>.part`` with a ``.meta`` sidecar holding the
    validator (ETag / Last-Modified). Resumes with ``Range`` + ``If-Range``,
    verifies the size, then atomically moves into ``data_dir``. Transient
    failures are retried with exponential backoff and jitter.
    """

    def __init__(
        self,
        cache_dir: Path | str,
        data_dir: Path | str,
        force: bool = False,
        max_attempts: int = 5,
        backoff: float = 1.0,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.data_dir = Path(data_dir)
        self.force = force
        self.max_attempts = max_attempts
        self.backoff = backoff
        self._session = requests.Session()

    def __enter__(self) -> Downloader:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._session.close()

    def download(self, url: str, file_name: str) -> DownloadResult:
        """Download *url* into ``data_dir/file_name``."""
        _create_directory(self.cache_dir)
        _create_directory(self.data_dir)

        dest = self.data_dir / file_name
        part = self.cache_dir / f"{file_name}.part"
        meta = self.cache_dir / f"{file_name}.part.meta"

        if dest.exists() and not self.force:
            if not _has_read_access(dest):
                raise PermissionError(f"No read access to {dest}")
            return DownloadResult(path=dest)

        if not _has_write_access(part):
            raise PermissionError(f"No write access to {part}")

        if not _has_write_access(dest):
            raise PermissionError(f"No write access to {dest}")

        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._attempt(url, dest, part, meta)
            except _RETRYABLE as e:
                if attempt == self.max_attempts:
                    log.error(f"Download failed after {attempt} attempts: {url}: {e}")
                    raise
                delay = self._delay(attempt, getattr(e, "retry_after", None))
                log.warning(
                    f"Download attempt {attempt}/{self.max_attempts} failed ({e}); "
                    f"retrying in {delay:.1f}s"
                )
                time.sleep(delay)

        raise AssertionError("unreachable")

    def _delay(self, attempt: int, retry_after: float | None) -> float:
        cap = min(MAX_BACKOFF, self.backoff * 2 ** (attempt - 1))
        delay = random.uniform(cap / 2, cap)
        if retry_after is not None:
            delay = max(delay, min(retry_after, MAX_BACKOFF))
        return delay

    @staticmethod
    def _read_meta(meta: Path, url: str) -> dict:
        try:
            data = json.loads(meta.read_text())
        except (OSError, ValueError):
            return {}
        return data if data.get("url") == url else {}

    def _attempt(
        self,
        url: str,
        dest: Path,
        part: Path,
        meta: Path,
    ) -> DownloadResult:
        offset = part.stat().st_size if part.exists() else 0
        saved = self._read_meta(meta, url)
        validator = saved.get("etag") or saved.get("last_modified")

        if offset and not validator:
            # Cannot prove the partial belongs to the current remote file.
            _unlink(part)
            offset = 0

        headers: dict[str, str] = {"Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
            headers["If-Range"] = validator

        with self._session.get(
            url, stream=True, headers=headers, timeout=TIMEOUT
        ) as resp:
            status = resp.status_code

            if status == 416:
                m = _UNSATISFIED_RANGE.match(resp.headers.get("Content-Range", ""))
                if offset and m and int(m.group(1)) == offset:
                    return self._finalize(part, meta, dest, saved)
                _unlink(part)
                _unlink(meta)
                raise _Transient("416, discarded stale partial")

            if status in RETRY_STATUSES:
                ra = resp.headers.get("Retry-After", "")
                raise _Transient(f"HTTP {status}", float(ra) if ra.isdigit() else None)

            if status not in (200, 206):
                resp.raise_for_status()
                raise requests.HTTPError(f"Unexpected status {status} for {url}")

            total: int | None = None
            if status == 206:
                m = _CONTENT_RANGE.match(resp.headers.get("Content-Range", ""))
                if not m or int(m.group(1)) != offset:
                    _unlink(part)
                    _unlink(meta)
                    raise _Transient("Content-Range does not match requested offset")
                total = int(m.group(2)) if m.group(2) != "*" else None
                mode = "ab"
            else:
                length = resp.headers.get("content-length")
                total = int(length) if length else None
                offset = 0
                mode = "wb"

            etag = resp.headers.get("ETag")
            raw_lm = resp.headers.get("Last-Modified")
            info = {
                "url": url,
                "etag": etag,
                "last_modified": raw_lm,
            }
            meta.write_text(json.dumps(info))

            log.info(
                f"Starting download: {url} -> {dest} "
                f"(size: {_human_readable_bytes(total)}"
                f"{f', resumed_at: {_human_readable_bytes(offset)}' if offset else ''})"
            )

            start = time.perf_counter()
            written = 0
            with DownloadProgressbar() as progress:
                progress.add(
                    f"Downloading {dest.name}", total=total, completed=offset
                )
                with part.open(mode, buffering=CHUNK_SIZE) as fh:
                    for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        written += len(chunk)
                        progress.update(advance=len(chunk))
                    fh.flush()
                    os.fsync(fh.fileno())

        size = part.stat().st_size
        if total is not None and size != total:
            if size > total:
                _unlink(part)
                _unlink(meta)
            raise _Transient(f"incomplete download ({size}/{total} bytes)")

        elapsed = time.perf_counter() - start
        log.info(
            f"Finished download: {dest} ({_human_readable_bytes(size)}) "
            f"avg speed: {_human_readable_bytes(int(written / elapsed) if elapsed else 0)}/s"
        )
        return self._finalize(part, meta, dest, info)

    @staticmethod
    def _finalize(part: Path, meta: Path, dest: Path, info: dict) -> DownloadResult:
        _atomic_move(part, dest)
        _unlink(meta)
        raw_lm = info.get("last_modified")
        return DownloadResult(
            path=dest,
            etag=info.get("etag"),
            last_modified=rfc2822_to_datetime(raw_lm) if raw_lm else None,
        )
