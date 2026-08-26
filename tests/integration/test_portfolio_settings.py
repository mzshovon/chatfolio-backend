from collections.abc import Callable

import pytest
from httpx import AsyncClient

from chatfolio.api.deps import get_llm_provider_factory
from chatfolio.main import app
from tests.factories.fake_llm import FakeLLMFactory
from tests.factories.publish_flow import authed_client as _authed_client


@pytest.fixture
def set_fake_llm() -> Callable[[str], None]:
    def _set(response: str) -> None:
        app.dependency_overrides[get_llm_provider_factory] = lambda: FakeLLMFactory(response)

    yield _set
    app.dependency_overrides.pop(get_llm_provider_factory, None)


async def _approve_both_sections(
    client: AsyncClient, headers: dict[str, str], set_fake_llm: Callable[[str], None]
) -> None:
    set_fake_llm("Generated content.")
    sections = (await client.get("/api/v1/sections", headers=headers)).json()
    for section in sections:
        await client.post(f"/api/v1/sections/{section['id']}/approve", headers=headers)


async def test_get_settings_auto_creates_with_generated_slug() -> None:
    client, headers = await _authed_client("settings-owner@example.com")
    response = await client.get("/api/v1/portfolio-settings", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["is_published"] is False
    assert body["slug"]
    assert body["subdomain"] == f"{body['slug']}.chatfolio.com"


async def test_publish_fails_without_name_or_approved_sections() -> None:
    client, headers = await _authed_client("publish-fail-owner@example.com")
    response = await client.post("/api/v1/portfolio-settings/publish", headers=headers)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "full name" in detail
    assert "intro" in detail
    assert "summary" in detail


async def test_publish_succeeds_once_requirements_met(set_fake_llm: Callable[[str], None]) -> None:
    client, headers = await _authed_client("publish-owner@example.com")
    await client.patch("/api/v1/profiles/me", headers=headers, json={"full_name": "Ada Lovelace"})
    await _approve_both_sections(client, headers, set_fake_llm)

    response = await client.post("/api/v1/portfolio-settings/publish", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["is_published"] is True
    assert body["published_at"] is not None


async def test_unpublish_hides_the_page(set_fake_llm: Callable[[str], None]) -> None:
    client, headers = await _authed_client("unpublish-owner@example.com")
    await client.patch("/api/v1/profiles/me", headers=headers, json={"full_name": "Ada Lovelace"})
    await _approve_both_sections(client, headers, set_fake_llm)
    await client.post("/api/v1/portfolio-settings/publish", headers=headers)

    response = await client.post("/api/v1/portfolio-settings/unpublish", headers=headers)
    assert response.status_code == 200
    assert response.json()["is_published"] is False


async def test_slug_update_tracks_previous_slug_and_enforces_uniqueness() -> None:
    client_a, headers_a = await _authed_client("slug-owner-a@example.com")
    original = (await client_a.get("/api/v1/portfolio-settings", headers=headers_a)).json()["slug"]

    rename_response = await client_a.patch(
        "/api/v1/portfolio-settings", headers=headers_a, json={"slug": "ada-lovelace-dev"}
    )
    assert rename_response.status_code == 200
    body = rename_response.json()
    assert body["slug"] == "ada-lovelace-dev"
    assert body["previous_slug"] == original

    client_b, headers_b = await _authed_client("slug-owner-b@example.com")
    conflict_response = await client_b.patch(
        "/api/v1/portfolio-settings", headers=headers_b, json={"slug": "ada-lovelace-dev"}
    )
    assert conflict_response.status_code == 409
