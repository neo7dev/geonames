"""Remote file metadata client.

Provides a reusable, session-based HTTP client for inspecting remote files
(HEAD requests, Last-Modified checks, content-length, etc.) without
downloading the full payload.
"""

from __future__ import annotations

from datetime import datetime

import requests
from pydantic import BaseModel, ConfigDict, Field, field_validator

from geonames_server.log.mylogger import get_logger
from geonames_server.utils.utils import rfc2822_to_datetime

log = get_logger()

_DEFAULT_TIMEOUT = 30


class RemoteFileMeta(BaseModel):
    """Parsed metadata from HTTP response headers."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    url: str
    status_code: int

    content_length: int | None = Field(None, alias="Content-Length")
    last_modified: datetime | None = Field(None, alias="Last-Modified")
    etag: str | None = Field(None, alias="ETag")
    content_type: str | None = Field(None, alias="Content-Type")
    accept_ranges: str | None = Field(None, alias="Accept-Ranges")

    @field_validator("last_modified", mode="before")
    @classmethod
    def parse_rfc2822_date(cls, v):
        if isinstance(v, str):
            return rfc2822_to_datetime(v)
        return v

    # ! Bad idea to strip quotes from ETag, some servers use weak ETags with W/ prefix and quotes are part of the value
    # @field_validator("etag", mode="before")
    # @classmethod
    # def clean_etag(cls, v):
    #     if isinstance(v, str):
    #         return v.strip('"')
    #     return v

    @property
    def supports_resume(self) -> bool:
        return self.accept_ranges == "bytes"


class RemoteFileClient:
    """Lightweight HTTP client for remote file inspection.

    Usage::

        with RemoteFileClient() as client:
            meta = client.head("https://download.geonames.org/export/dump/allCountries.zip")
            if meta.last_modified:
                print(meta.last_modified)
    """

    def __init__(self, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._session = requests.Session()
        self._timeout = timeout

    def __enter__(self) -> RemoteFileClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._session.close()

    def head(self, url: str) -> RemoteFileMeta:
        """Send a HEAD request and return parsed metadata."""
        log.debug(f"HEAD {url}")
        resp = self._session.head(url, timeout=self._timeout, allow_redirects=True)
        resp.raise_for_status()

        return RemoteFileMeta(
            url=url,
            status_code=resp.status_code,
            **dict(resp.headers),
        )

    def has_changed(
        self,
        url: str,
        known_etag: str | None = None,
        known_last_modified: datetime | None = None,
    ) -> bool:
        """Check if a remote file has changed using conditional headers.

        Sends a conditional GET/HEAD with If-None-Match / If-Modified-Since.
        Returns False if the server responds 304 Not Modified, True otherwise.
        """
        headers: dict[str, str] = {}
        if known_etag:
            headers["If-None-Match"] = known_etag
        if known_last_modified:
            headers["If-Modified-Since"] = known_last_modified

        if not headers:
            return True  # no baseline to compare — assume changed

        resp = self._session.head(
            url, headers=headers, timeout=self._timeout, allow_redirects=True
        )
        return resp.status_code != 304

    def exists(self, url: str) -> bool:
        """Return True if the remote URL responds with 2xx to a HEAD request."""
        try:
            resp = self._session.head(url, timeout=self._timeout, allow_redirects=True)
            return resp.ok
        except requests.RequestException:
            return False
