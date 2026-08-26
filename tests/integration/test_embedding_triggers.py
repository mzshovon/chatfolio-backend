from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from chatfolio.api.deps import get_job_queue, get_llm_provider_factory, get_vector_store
from chatfolio.main import app
from tests.factories.fake_llm import FakeLLMFactory
from tests.factories.fake_vectorstore import FakeVectorStore


class SpyJobQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, tuple[object, ...]]] = []

    async def enqueue_job(self, function: str, *args: object) -> None:
        self.enqueued.append((function, args))


@pytest.fixture
async def spy_vector_store() -> AsyncGenerator[FakeVectorStore]:
    store = FakeVectorStore()
    app.dependency_overrides[get_vector_store] = lambda: store
    yield store
    # Restore a fake (never pop to "no override") — conftest.py sets a global fake default for
    # every test in the suite so nothing ever falls through to a real Chroma connection; popping
    # here would silently remove that default for every test that runs after this one.
    app.dependency_overrides[get_vector_store] = lambda: FakeVectorStore()


@pytest.fixture
async def spy_job_queue() -> AsyncGenerator[SpyJobQueue]:
    queue = SpyJobQueue()
    app.dependency_overrides[get_job_queue] = lambda: queue
    yield queue
    app.dependency_overrides.pop(get_job_queue, None)


async def _authed_client(email: str) -> tuple[AsyncClient, dict[str, str]]:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    password = "supersecret123"
    await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = login.json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


async def test_creating_experience_enqueues_embed_job(spy_job_queue: SpyJobQueue) -> None:
    client, headers = await _authed_client("embed-experience-owner@example.com")
    response = await client.post(
        "/api/v1/profiles/me/experience",
        headers=headers,
        json={"company": "Acme", "role": "Backend Engineer", "description": "Built APIs."},
    )
    assert response.status_code == 201
    experience_id = response.json()["id"]

    assert len(spy_job_queue.enqueued) == 1
    function, args = spy_job_queue.enqueued[0]
    assert function == "embed_content_job"
    _, source_type, source_id, chunk_text = args
    assert source_type == "experience"
    assert source_id == experience_id
    assert "Backend Engineer" in chunk_text
    assert "Acme" in chunk_text


async def test_updating_experience_re_enqueues_embed_job(spy_job_queue: SpyJobQueue) -> None:
    client, headers = await _authed_client("embed-experience-update-owner@example.com")
    create_response = await client.post(
        "/api/v1/profiles/me/experience",
        headers=headers,
        json={"company": "Acme", "role": "Engineer"},
    )
    experience_id = create_response.json()["id"]
    spy_job_queue.enqueued.clear()

    await client.patch(
        f"/api/v1/profiles/me/experience/{experience_id}",
        headers=headers,
        json={"role": "Staff Engineer"},
    )

    assert len(spy_job_queue.enqueued) == 1
    _, args = spy_job_queue.enqueued[0]
    assert "Staff Engineer" in args[-1]


async def test_deleting_experience_deletes_embedding(
    spy_job_queue: SpyJobQueue, spy_vector_store: FakeVectorStore
) -> None:
    client, headers = await _authed_client("embed-experience-delete-owner@example.com")
    create_response = await client.post(
        "/api/v1/profiles/me/experience",
        headers=headers,
        json={"company": "Acme", "role": "Engineer"},
    )
    experience_id = create_response.json()["id"]

    delete_response = await client.delete(
        f"/api/v1/profiles/me/experience/{experience_id}", headers=headers
    )
    assert delete_response.status_code == 204
    assert spy_vector_store.deleted_ids == [f"experience:{experience_id}"]


async def test_approving_section_enqueues_embed_job(spy_job_queue: SpyJobQueue) -> None:
    app.dependency_overrides[get_llm_provider_factory] = lambda: FakeLLMFactory("Generated intro.")
    try:
        client, headers = await _authed_client("embed-section-owner@example.com")
        sections = (await client.get("/api/v1/sections", headers=headers)).json()
        intro = next(s for s in sections if s["section_type"] == "intro")
        spy_job_queue.enqueued.clear()

        response = await client.post(f"/api/v1/sections/{intro['id']}/approve", headers=headers)
        assert response.status_code == 200

        assert len(spy_job_queue.enqueued) == 1
        function, args = spy_job_queue.enqueued[0]
        assert function == "embed_content_job"
        assert args[1] == "portfolio_section"
        assert args[2] == intro["id"]
        assert args[3] == "Generated intro."
    finally:
        app.dependency_overrides.pop(get_llm_provider_factory, None)


async def test_editing_approved_section_deletes_stale_embedding(
    spy_vector_store: FakeVectorStore,
) -> None:
    app.dependency_overrides[get_llm_provider_factory] = lambda: FakeLLMFactory("Generated intro.")
    try:
        client, headers = await _authed_client("embed-section-edit-owner@example.com")
        sections = (await client.get("/api/v1/sections", headers=headers)).json()
        intro = next(s for s in sections if s["section_type"] == "intro")
        await client.post(f"/api/v1/sections/{intro['id']}/approve", headers=headers)

        await client.patch(
            f"/api/v1/sections/{intro['id']}", headers=headers, json={"content": "edited"}
        )

        assert spy_vector_store.deleted_ids == [f"portfolio_section:{intro['id']}"]
    finally:
        app.dependency_overrides.pop(get_llm_provider_factory, None)
