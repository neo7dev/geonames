"""Data routes need a valid API key in X-API-Key; health and docs stay public."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, text, update

from geonames_server.api import dependencies
from geonames_server.database.models.accounts import ApiKey

ENABLED = "geonames_server.api.dependencies.settings.enabled_datasets"

# One route per protected router family
DATA_PATHS = [
    "/cities15000/",
    "/cities15000/filter",
    "/cities15000/1",
    "/modifications/",
    "/deletions/",
    "/metadata/",
]


@pytest.fixture(autouse=True)
def _cities15000_enabled():
    with patch(ENABLED, ["cities15000"]):
        yield


@pytest.mark.asyncio
@pytest.mark.parametrize("path", DATA_PATHS)
async def test_missing_key_is_rejected(auth_client, path):
    resp = await auth_client.get(path)

    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "ApiKey"
    assert "X-API-Key" in resp.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", DATA_PATHS)
async def test_unknown_key_is_rejected(auth_client, path):
    resp = await auth_client.get(path, headers={"X-API-Key": "gn_not-a-real-key"})

    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "ApiKey"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", DATA_PATHS)
async def test_revoked_key_is_rejected(
    auth_client, async_session, make_user, make_key, path
):
    user_id = await make_user()
    key = await make_key(user_id)
    await async_session.execute(
        update(ApiKey).where(ApiKey.user_id == user_id).values(revoked_at=func.now())
    )
    await async_session.commit()

    resp = await auth_client.get(path, headers={"X-API-Key": key})

    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/cities15000/",
        "/cities15000/filter",
        "/modifications/",
        "/deletions/",
        "/metadata/",
    ],
)
async def test_valid_key_is_served(auth_client, make_user, make_key, path):
    key = await make_key(await make_user())

    resp = await auth_client.get(path, headers={"X-API-Key": key})

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_key_is_not_accepted_as_bearer_token(auth_client, make_user, make_key):
    key = await make_key(await make_user())

    resp = await auth_client.get(
        "/deletions/", headers={"Authorization": f"Bearer {key}"}
    )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_token_is_not_accepted_as_api_key(auth_client, make_user, make_jwt):
    token = make_jwt(await make_user())

    resp = await auth_client.get("/deletions/", headers={"X-API-Key": token})

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_missing_key_is_rejected_before_the_dataset_build_check(
    auth_client, make_user, make_key
):
    dependencies._built.discard("cities15000")
    with patch(
        "geonames_server.api.dependencies._is_built", AsyncMock(return_value=False)
    ):
        assert (await auth_client.get("/cities15000/")).status_code == 401

        key = await make_key(await make_user())
        resp = await auth_client.get("/cities15000/", headers={"X-API-Key": key})
        assert resp.status_code == 503


@pytest.mark.asyncio
async def test_deleting_the_user_disables_their_keys(
    auth_client, async_session, make_user, make_key
):
    user_id = await make_user()
    key = await make_key(user_id)
    await async_session.execute(
        text("DELETE FROM auth.users WHERE id = :id"), {"id": user_id}
    )
    await async_session.commit()

    resp = await auth_client.get("/deletions/", headers={"X-API-Key": key})

    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "status"),
    [
        pytest.param("banned_until = now() + interval '1 hour'", 401, id="banned"),
        pytest.param("banned_until = now() - interval '1 hour'", 200, id="ban-expired"),
        pytest.param("deleted_at = now()", 401, id="soft-deleted"),
    ],
)
async def test_banned_or_soft_deleted_users_keys_are_refused(
    auth_client, async_session, make_user, make_key, change, status
):
    user_id = await make_user()
    key = await make_key(user_id)
    await async_session.execute(
        text(f"UPDATE auth.users SET {change} WHERE id = :id"), {"id": user_id}
    )
    await async_session.commit()

    resp = await auth_client.get("/deletions/", headers={"X-API-Key": key})

    assert resp.status_code == status


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_docs_are_public(auth_client, path):
    assert (await auth_client.get(path)).status_code == 200


@pytest.mark.asyncio
async def test_health_is_public(auth_client):
    assert (await auth_client.get("/health")).status_code != 401


@pytest.mark.asyncio
async def test_openapi_declares_the_key_for_protected_routes(auth_client):
    spec = (await auth_client.get("/openapi.json")).json()
    schemes = spec["components"]["securitySchemes"]
    paths = spec["paths"]

    assert schemes["APIKeyHeader"] == {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "API key created on /portal/keys",
    }
    assert schemes["HTTPBearer"]["scheme"] == "bearer"
    assert {"APIKeyHeader": []} in paths["/cities15000/"]["get"]["security"]
    assert {"APIKeyHeader": []} in paths["/sync/"]["post"]["security"]
    assert {"HTTPBearer": []} in paths["/portal/keys"]["post"]["security"]
    assert "security" not in paths["/health"]["get"]
