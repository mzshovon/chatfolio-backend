from collections.abc import Callable

import pytest
from sqlalchemy import select

from chatfolio.api.deps import get_llm_provider_factory
from chatfolio.db.session import get_sessionmaker
from chatfolio.main import app
from chatfolio.models.chat import ChatSession, RecruiterMetadata
from chatfolio.models.chatfolio import PublicChatfolio
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
    response = await client.get("/api/v1/public/chatfolio/does-not-exist")
    assert response.status_code == 404


async def test_published_slug_returns_full_page(set_fake_llm: Callable[[str], None]) -> None:
    client, _headers, slug = await publish_full_profile(
        "public-owner@example.com", set_fake_llm, "ada-public-1"
    )

    response = await client.get(f"/api/v1/public/chatfolio/{slug}")
    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Ada Lovelace"
    assert body["intro"] == "Generated content."
    assert body["summary"] == "Generated content."
    assert len(body["experiences"]) == 1
    assert body["experiences"][0]["company"] == "Acme"
    assert body["recruiter_count"] == 0


async def test_draft_section_content_is_never_exposed_publicly(
    set_fake_llm: Callable[[str], None],
) -> None:
    client, headers, slug = await publish_full_profile(
        "public-draft-owner@example.com", set_fake_llm, "ada-public-2"
    )
    sections = (await client.get("/api/v1/sections", headers=headers)).json()
    intro = next(s for s in sections if s["section_type"] == "intro")

    # Editing resets the section to draft — the public page must stop showing it immediately.
    await client.patch(
        f"/api/v1/sections/{intro['id']}", headers=headers, json={"content": "unreviewed text"}
    )

    response = await client.get(f"/api/v1/public/chatfolio/{slug}")
    body = response.json()
    assert body["intro"] is None
    assert "unreviewed text" not in str(body)


async def _add_session_with_metadata(
    slug: str, *, name: str | None = None, company: str | None = None
) -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(select(PublicChatfolio).where(PublicChatfolio.slug == slug))
        chatfolio = result.scalar_one()

        chat_session = ChatSession(chatfolio_id=chatfolio.id)
        session.add(chat_session)
        await session.flush()

        session.add(RecruiterMetadata(session_id=chat_session.id, name=name, company=company))
        await session.commit()


async def _add_session_without_metadata(slug: str) -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(select(PublicChatfolio).where(PublicChatfolio.slug == slug))
        chatfolio = result.scalar_one()
        session.add(ChatSession(chatfolio_id=chatfolio.id))
        await session.commit()


async def test_recruiter_count_only_includes_sessions_with_name_or_company(
    set_fake_llm: Callable[[str], None],
) -> None:
    client, _headers, slug = await publish_full_profile(
        "public-recruiter-count-owner@example.com", set_fake_llm, "ada-public-5"
    )

    await _add_session_with_metadata(slug, name="Rafi")
    await _add_session_with_metadata(slug, company="Cefalo")
    await _add_session_with_metadata(slug)  # metadata row exists but both fields null
    await _add_session_without_metadata(slug)  # no RecruiterMetadata row at all

    response = await client.get(f"/api/v1/public/chatfolio/{slug}")
    assert response.status_code == 200
    assert response.json()["recruiter_count"] == 2


async def test_unpublishing_makes_page_unavailable(set_fake_llm: Callable[[str], None]) -> None:
    client, headers, slug = await publish_full_profile(
        "public-unpublish-owner@example.com", set_fake_llm, "ada-public-3"
    )
    await client.post("/api/v1/portfolio-settings/unpublish", headers=headers)

    response = await client.get(f"/api/v1/public/chatfolio/{slug}")
    assert response.status_code == 404


async def test_renaming_slug_redirects_from_old_slug(set_fake_llm: Callable[[str], None]) -> None:
    client, headers, old_slug = await publish_full_profile(
        "public-redirect-owner@example.com", set_fake_llm, "ada-public-4"
    )
    await client.patch(
        "/api/v1/portfolio-settings", headers=headers, json={"slug": "ada-public-4-new"}
    )

    response = await client.get(f"/api/v1/public/chatfolio/{old_slug}", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/api/v1/public/chatfolio/ada-public-4-new"

    new_response = await client.get("/api/v1/public/chatfolio/ada-public-4-new")
    assert new_response.status_code == 200


async def test_search_returns_card_fields_and_filters(
    set_fake_llm: Callable[[str], None],
) -> None:
    client, headers, slug = await publish_full_profile(
        "search-owner@example.com", set_fake_llm, "ada-search-1"
    )
    await client.patch(
        "/api/v1/profiles/me",
        headers=headers,
        json={
            "location": "Dhaka",
            "job_type": "remote",
            "avatar_url": "https://example.com/a.png",
            "notice_period_days": 0,
            "work_hours": {"start": "10:00", "end": "23:00", "timezone": "Asia/Dhaka"},
        },
    )
    await client.post(
        "/api/v1/profiles/me/skills", headers=headers, json={"name": "Python"}
    )

    response = await client.get(
        "/api/v1/public/chatfolio/search", params={"q": "Python Dhaka remote"}
    )
    assert response.status_code == 200
    card = next(c for c in response.json() if c["slug"] == slug)
    assert card["title"] == "Backend Engineer"
    assert card["location"] == "Dhaka"
    assert card["avatar_url"] == "https://example.com/a.png"
    assert card["headline"] == "Generated content."
    assert card["current_company"] == "Acme"
    assert card["job_type"] == "remote"
    assert card["notice_period_days"] == 0
    assert card["top_skills"] == ["Python"]
    assert card["work_hours"]["timezone"] == "Asia/Dhaka"

    def slugs(body: list[dict]) -> list[str]:
        return [c["slug"] for c in body]

    base = "/api/v1/public/chatfolio/search"
    assert slug in slugs((await client.get(base, params={"work": "remote", "start": "immediately"})).json())
    assert slug not in slugs((await client.get(base, params={"work": "onsite"})).json())
    assert slug not in slugs((await client.get(base, params={"q": "golang"})).json())
    overlap = {"overlap": "true", "my_start": "09:00", "my_end": "18:00", "my_timezone": "Asia/Dhaka"}
    assert slug in slugs((await client.get(base, params=overlap)).json())
    overlap["my_start"], overlap["my_end"] = "03:00", "05:00"
    assert slug not in slugs((await client.get(base, params=overlap)).json())
    assert (await client.get(base, params={"overlap": "true"})).status_code == 422
