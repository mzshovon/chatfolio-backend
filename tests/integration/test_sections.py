from collections.abc import Callable

import pytest
from httpx import ASGITransport, AsyncClient

from chatfolio.api.deps import get_llm_provider_factory
from chatfolio.main import app
from tests.factories.fake_llm import FakeLLMFactory


@pytest.fixture
def set_fake_llm() -> Callable[[str], None]:
    def _set(response: str) -> None:
        app.dependency_overrides[get_llm_provider_factory] = lambda: FakeLLMFactory(response)

    yield _set
    app.dependency_overrides.pop(get_llm_provider_factory, None)


async def _authed_client(
    email: str = "sections-owner@example.com",
) -> tuple[AsyncClient, dict[str, str]]:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    password = "supersecret123"
    await client.post("/v1/auth/register", json={"email": email, "password": password})
    login = await client.post("/v1/auth/login", json={"email": email, "password": password})
    token = login.json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


async def test_list_sections_generates_both_on_first_call(
    set_fake_llm: Callable[[str], None],
) -> None:
    set_fake_llm("I am a backend engineer who loves building reliable systems.")
    client, headers = await _authed_client()
    await client.patch(
        "/v1/profiles/me",
        headers=headers,
        json={"full_name": "Ada Lovelace", "title": "Backend Engineer"},
    )

    response = await client.get("/v1/sections", headers=headers)
    assert response.status_code == 200
    sections = response.json()
    assert {s["section_type"] for s in sections} == {"intro", "summary"}
    for section in sections:
        assert section["status"] == "draft"
        assert section["generated_by"] == "ai"
        assert section["version"] == 1
        assert section["content"] == "I am a backend engineer who loves building reliable systems."


async def test_list_sections_is_stable_on_second_call(set_fake_llm: Callable[[str], None]) -> None:
    set_fake_llm("first generation")
    client, headers = await _authed_client()

    first = await client.get("/v1/sections", headers=headers)
    first_ids = {s["id"] for s in first.json()}

    set_fake_llm("a different response that should not be used")
    second = await client.get("/v1/sections", headers=headers)
    second_ids = {s["id"] for s in second.json()}

    assert first_ids == second_ids
    for section in second.json():
        assert section["content"] == "first generation"


async def test_regenerate_bumps_version_and_content(set_fake_llm: Callable[[str], None]) -> None:
    set_fake_llm("version one content")
    client, headers = await _authed_client()
    sections = (await client.get("/v1/sections", headers=headers)).json()
    intro = next(s for s in sections if s["section_type"] == "intro")

    set_fake_llm("version two content")
    response = await client.post(f"/v1/sections/{intro['id']}/regenerate", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 2
    assert body["content"] == "version two content"
    assert body["generated_by"] == "ai"
    assert body["status"] == "draft"


async def test_update_content_marks_manual_and_resets_approval(
    set_fake_llm: Callable[[str], None],
) -> None:
    set_fake_llm("ai generated content")
    client, headers = await _authed_client()
    sections = (await client.get("/v1/sections", headers=headers)).json()
    intro = next(s for s in sections if s["section_type"] == "intro")

    approve_response = await client.post(f"/v1/sections/{intro['id']}/approve", headers=headers)
    assert approve_response.json()["status"] == "approved"

    edit_response = await client.patch(
        f"/v1/sections/{intro['id']}", headers=headers, json={"content": "candidate-edited text"}
    )
    assert edit_response.status_code == 200
    body = edit_response.json()
    assert body["content"] == "candidate-edited text"
    assert body["generated_by"] == "manual"
    assert body["status"] == "draft"


async def test_approve_flips_status(set_fake_llm: Callable[[str], None]) -> None:
    set_fake_llm("some content")
    client, headers = await _authed_client()
    sections = (await client.get("/v1/sections", headers=headers)).json()
    intro = next(s for s in sections if s["section_type"] == "intro")

    response = await client.post(f"/v1/sections/{intro['id']}/approve", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "approved"


async def test_generation_failure_returns_clean_503_not_raw_traceback() -> None:
    class BrokenLLMFactory:
        def for_task(self, task: object) -> object:
            raise RuntimeError("LLM_DEEPSEEK_API_KEY is not configured.")

    app.dependency_overrides[get_llm_provider_factory] = lambda: BrokenLLMFactory()
    try:
        client, headers = await _authed_client("broken-llm-owner@example.com")
        response = await client.get("/v1/sections", headers=headers)
        assert response.status_code == 503
        assert response.json() == {
            "detail": "AI section generation is not available right now. Please try again later."
        }
    finally:
        app.dependency_overrides.pop(get_llm_provider_factory, None)


async def test_cannot_access_another_users_section(set_fake_llm: Callable[[str], None]) -> None:
    set_fake_llm("owner content")
    client, headers = await _authed_client("sections-owner-a@example.com")
    sections = (await client.get("/v1/sections", headers=headers)).json()
    intro = next(s for s in sections if s["section_type"] == "intro")

    _, other_headers = await _authed_client("sections-owner-b@example.com")
    response = await client.patch(
        f"/v1/sections/{intro['id']}", headers=other_headers, json={"content": "hacked"}
    )
    assert response.status_code == 404
