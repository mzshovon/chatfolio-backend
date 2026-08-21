from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select

from chatfolio.api.deps import get_settings
from chatfolio.config.settings import get_settings as _real_get_settings
from chatfolio.db.session import get_sessionmaker
from chatfolio.main import app
from chatfolio.models.profile import CandidateProfile
from chatfolio.models.user import User
from tests.factories.publish_flow import authed_client


@pytest.fixture
async def custom_domains_enabled() -> AsyncGenerator[None]:
    base = _real_get_settings()
    enabled = base.model_copy(deep=True)
    enabled.features.enable_custom_domains = True
    app.dependency_overrides[get_settings] = lambda: enabled
    yield
    app.dependency_overrides.pop(get_settings, None)


async def test_domain_endpoints_disabled_by_default() -> None:
    client, headers = await authed_client("domain-disabled@example.com")

    assert (await client.get("/v1/portfolio-settings/domain", headers=headers)).status_code == 404
    post = await client.post(
        "/v1/portfolio-settings/domain", headers=headers, json={"domain": "candidate.example.com"}
    )
    assert post.status_code == 404
    assert (
        await client.delete("/v1/portfolio-settings/domain", headers=headers)
    ).status_code == 404


async def test_add_get_remove_domain_when_enabled(custom_domains_enabled: None) -> None:
    client, headers = await authed_client("domain-crud@example.com")

    missing = await client.get("/v1/portfolio-settings/domain", headers=headers)
    assert missing.status_code == 404

    add = await client.post(
        "/v1/portfolio-settings/domain", headers=headers, json={"domain": "candidate.example.com"}
    )
    assert add.status_code == 201
    body = add.json()
    assert body["domain"] == "candidate.example.com"
    assert body["is_verified"] is False
    assert len(body["verification_token"]) == 32

    fetched = await client.get("/v1/portfolio-settings/domain", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["domain"] == "candidate.example.com"

    deleted = await client.delete("/v1/portfolio-settings/domain", headers=headers)
    assert deleted.status_code == 204

    gone = await client.get("/v1/portfolio-settings/domain", headers=headers)
    assert gone.status_code == 404


async def test_domain_already_taken_returns_conflict(custom_domains_enabled: None) -> None:
    client_a, headers_a = await authed_client("domain-taken-a@example.com")
    client_b, headers_b = await authed_client("domain-taken-b@example.com")

    first = await client_a.post(
        "/v1/portfolio-settings/domain", headers=headers_a, json={"domain": "shared.example.com"}
    )
    assert first.status_code == 201

    second = await client_b.post(
        "/v1/portfolio-settings/domain", headers=headers_b, json={"domain": "shared.example.com"}
    )
    assert second.status_code == 409


async def test_adding_domain_replaces_previous_one(custom_domains_enabled: None) -> None:
    client, headers = await authed_client("domain-replace@example.com")

    await client.post(
        "/v1/portfolio-settings/domain", headers=headers, json={"domain": "first.example.com"}
    )
    second = await client.post(
        "/v1/portfolio-settings/domain", headers=headers, json={"domain": "second.example.com"}
    )
    assert second.status_code == 201

    fetched = await client.get("/v1/portfolio-settings/domain", headers=headers)
    assert fetched.json()["domain"] == "second.example.com"


async def test_new_profile_defaults_to_free_plan_with_no_usage_limits() -> None:
    client, headers = await authed_client("plan-default@example.com")
    await client.patch("/v1/profiles/me", headers=headers, json={"full_name": "Someone"})

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(select(User).where(User.email == "plan-default@example.com"))
        user = result.scalar_one()
        profile_result = await session.execute(
            select(CandidateProfile).where(CandidateProfile.user_id == user.id)
        )
        profile = profile_result.scalar_one()
        assert profile.plan == "free"
        assert profile.usage_limits == {}


async def test_domain_endpoints_require_auth() -> None:
    client, _ = await authed_client("domain-noauth@example.com")
    response = await client.get("/v1/portfolio-settings/domain")
    assert response.status_code == 401
