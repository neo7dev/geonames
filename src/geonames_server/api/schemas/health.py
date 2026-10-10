from typing import Literal

from pydantic import BaseModel


class DatasetsHealth(BaseModel):
    # Startup preparation (remote metadata check + rebuilds) running in the background
    startup: Literal["pending", "running", "done", "failed"]
    # Enabled datasets that are built, and those still waiting for a (re)build
    ready: list[str]
    pending: list[str]


class HealthResponse(BaseModel):
    # error: database unreachable (HTTP 503). degraded: serving, but Redis is down,
    # startup failed or some datasets are not built yet.
    status: Literal["ok", "degraded", "error"]
    # The app version, also the Docker image's tag
    version: str
    database: Literal["ok", "error"]
    redis: Literal["ok", "error", "not_configured"]
    datasets: DatasetsHealth
