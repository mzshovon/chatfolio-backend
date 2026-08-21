from collections.abc import Callable

import pytest

from chatfolio.api.deps import get_llm_provider_factory
from chatfolio.main import app
from tests.factories.fake_llm import FakeLLMFactory
from tests.factories.publish_flow import authed_client, publish_full_profile


@pytest.fixture
def set_fake_llm() -> Callable[[str], None]:
    def _set(response: str) -> None:
        app.dependency_overrides[get_llm_provider_factory] = lambda: FakeLLMFactory(response)

    yield _set
    app.dependency_overrides.pop(get_llm_provider_factory, None)


async def test_unpublished_slug_returns_404() -> None:
    client, _ = await authed_client("unpublished-owner@example.com")
    response = await client.get("/v1/public/chatfolio/does-not-exist")
    assert response.status_code == 404


async def test_published_slug_returns_full_page(set_fake_llm: Callable[[str], None]) -> None:
    client, _headers, slug = await publish_full_profile(
        "public-owner@example.com", set_fake_llm, "ada-public-1"
    )

    response = await client.get(f"/v1/public/chatfolio/{slug}")
    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Ada Lovelace"
    assert body["intro"] == "Generated content."
    assert body["summary"] == "Generated content."
    assert len(body["experiences"]) == 1
    assert body["experiences"][0]["company"] == "Acme"


async def test_draft_section_content_is_never_exposed_publicly(
    set_fake_llm: Callable[[str], None],
) -> None:
    client, headers, slug = await publish_full_profile(
        "public-draft-owner@example.com", set_fake_llm, "ada-public-2"
    )
    sections = (await client.get("/v1/sections", headers=headers)).json()
    intro = next(s for s in sections if s["section_type"] == "intro")

    # Editing resets the section to draft — the public page must stop showing it immediately.
    await client.patch(
        f"/v1/sections/{intro['id']}", headers=headers, json={"content": "unreviewed text"}
    )

    response = await client.get(f"/v1/public/chatfolio/{slug}")
    body = response.json()
    assert body["intro"] is None
    assert "unreviewed text" not in str(body)


async def test_unpublishing_makes_page_unavailable(set_fake_llm: Callable[[str], None]) -> None:
    client, headers, slug = await publish_full_profile(
        "public-unpublish-owner@example.com", set_fake_llm, "ada-public-3"
    )
    await client.post("/v1/portfolio-settings/unpublish", headers=headers)

    response = await client.get(f"/v1/public/chatfolio/{slug}")
    assert response.status_code == 404


async def test_renaming_slug_redirects_from_old_slug(set_fake_llm: Callable[[str], None]) -> None:
    client, headers, old_slug = await publish_full_profile(
        "public-redirect-owner@example.com", set_fake_llm, "ada-public-4"
    )
    await client.patch("/v1/portfolio-settings", headers=headers, json={"slug": "ada-public-4-new"})

    response = await client.get(f"/v1/public/chatfolio/{old_slug}", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/v1/public/chatfolio/ada-public-4-new"

    new_response = await client.get("/v1/public/chatfolio/ada-public-4-new")
    assert new_response.status_code == 200
