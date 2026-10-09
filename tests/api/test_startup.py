"""The lifespan serves the API before datasets are built, then starts the scheduler."""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from geonames_server.api import app as app_module

MODULE = "geonames_server.api.app"


@pytest.fixture
def startup_mocks():
    release_build = threading.Event()
    build_finished = threading.Event()

    def _slow_build():
        release_build.wait(timeout=5)
        build_finished.set()
        return []

    with (
        patch(f"{MODULE}.Connection"),
        patch(f"{MODULE}.db_migrations"),
        patch(f"{MODULE}.seed_dataset_metadata"),
        patch(f"{MODULE}.check_remote_metadata") as check_remote,
        patch(f"{MODULE}.build_all_datasets", side_effect=_slow_build) as build,
        patch(f"{MODULE}.start_scheduler") as start,
        patch(f"{MODULE}.stop_scheduler") as stop,
    ):
        yield {
            "release_build": release_build,
            "build_finished": build_finished,
            "check_remote": check_remote,
            "build": build,
            "start": start,
            "stop": stop,
        }


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_api_serves_while_datasets_build(startup_mocks):
    app = FastAPI()

    async with app_module.lifespan(app):
        # The lifespan has yielded although the build is still blocked.
        await _wait_for(lambda: startup_mocks["build"].called)
        assert app.state.dataset_startup == "running"
        startup_mocks["start"].assert_not_called()

        startup_mocks["release_build"].set()
        await _wait_for(lambda: startup_mocks["start"].called)
        assert app.state.dataset_startup == "done"

    startup_mocks["check_remote"].assert_called_once()
    startup_mocks["stop"].assert_called_once()


@pytest.mark.asyncio
async def test_scheduler_starts_even_if_startup_fails(startup_mocks):
    startup_mocks["check_remote"].side_effect = RuntimeError("geonames.org down")
    app = FastAPI()

    async with app_module.lifespan(app):
        await _wait_for(lambda: startup_mocks["start"].called)
        assert app.state.dataset_startup == "failed"

    startup_mocks["build"].assert_not_called()


@pytest.mark.asyncio
async def test_scheduler_not_started_after_shutdown(startup_mocks):
    app = FastAPI()

    async with app_module.lifespan(app):
        await _wait_for(lambda: startup_mocks["build"].called)

    # Shutdown happened mid-build; the build finishing later must not start it.
    startup_mocks["release_build"].set()
    assert startup_mocks["build_finished"].wait(timeout=5)
    await asyncio.sleep(0.05)
    startup_mocks["start"].assert_not_called()


@pytest.mark.asyncio
async def test_lifespan_shares_one_redis_client(startup_mocks):
    app = FastAPI()
    redis = AsyncMock()

    with (
        patch.object(app_module.settings, "redis_url", "redis://redis:6379/0"),
        patch(f"{MODULE}.Redis.from_url", return_value=redis) as from_url,
    ):
        async with app_module.lifespan(app):
            assert app.state.redis is redis
            startup_mocks["release_build"].set()

    from_url.assert_called_once()
    redis.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifespan_without_redis_url(startup_mocks):
    app = FastAPI()

    with patch.object(app_module.settings, "redis_url", None):
        async with app_module.lifespan(app):
            assert app.state.redis is None
            startup_mocks["release_build"].set()


@pytest.mark.asyncio
async def test_startup_failed_when_a_dataset_build_fails(startup_mocks):
    startup_mocks["build"].side_effect = None
    startup_mocks["build"].return_value = ["cities15000"]
    app = FastAPI()

    async with app_module.lifespan(app):
        await _wait_for(lambda: startup_mocks["start"].called)
        assert app.state.dataset_startup == "failed"


def test_prepare_datasets_tolerates_closed_loop():
    loop = MagicMock()
    loop.call_soon_threadsafe.side_effect = RuntimeError("Event loop is closed")
    app = FastAPI()

    with (
        patch(f"{MODULE}.check_remote_metadata"),
        patch(f"{MODULE}.build_all_datasets", return_value=[]),
    ):
        app_module._prepare_datasets(app, [], loop, threading.Event())

    assert app.state.dataset_startup == "done"
