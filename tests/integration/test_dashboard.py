import json
import uuid
from collections.abc import Callable

import pytest

from chatfolio.api.deps import get_llm_provider_factory, get_vector_store
from chatfolio.main import app
from tests.factories.fake_llm import FakeLLMFactory
from tests.factories.fake_vectorstore import FakeVectorStore
from tests.factories.publish_flow import authed_client, publish_full_profile


@pytest.fixture
def set_fake_llm() -> Callable[[str], None]:
    def _set(response: str) -> None:
        app.dependency_overrides[get_llm_provider_factory] = lambda: FakeLLMFactory(response)

    yield _set
    app.dependency_overrides.pop(get_llm_provider_factory, None)


def _intent_response(intent: str, **context: str) -> str:
    return json.dumps({"intent": intent, "recruiter_context": context})


async def _send_recruiter_message(slug: str, recruiter_email: str, content: str) -> None:
    client, _ = await authed_client(recruiter_email)
    session_id = (await client.post(f"/api/v1/public/chat/{slug}/sessions")).json()["session_id"]

    app.dependency_overrides[get_llm_provider_factory] = lambda: FakeLLMFactory(
        _intent_response("general_introduction", company="Acme Corp")
    )
    app.dependency_overrides[get_vector_store] = lambda: FakeVectorStore(query_results=[])
    try:
        await client.post(
            f"/api/v1/public/chat/sessions/{session_id}/messages", json={"content": content}
        )
    finally:
        app.dependency_overrides.pop(get_llm_provider_factory, None)
        app.dependency_overrides[get_vector_store] = lambda: FakeVectorStore()


async def test_list_conversations_returns_owned_session_with_message_count(
    set_fake_llm: Callable[[str], None],
) -> None:
    _, owner_headers, slug = await publish_full_profile(
        "dash-list-owner@example.com", set_fake_llm, "dash-1"
    )
    await _send_recruiter_message(slug, "dash-list-recruiter@example.com", "Hi there!")

    owner_client, _ = await authed_client("dash-list-owner@example.com")
    response = await owner_client.get("/api/v1/dashboard/conversations", headers=owner_headers)

    assert response.status_code == 200
    conversations = response.json()
    assert len(conversations) == 1
    assert conversations[0]["message_count"] == 2
    assert conversations[0]["reviewed_by_candidate"] is False
    assert conversations[0]["recruiter_metadata"]["company"] == "Acme Corp"


async def test_list_conversations_excludes_sessions_with_no_messages(
    set_fake_llm: Callable[[str], None],
) -> None:
    _, owner_headers, slug = await publish_full_profile(
        "dash-empty-owner@example.com", set_fake_llm, "dash-empty"
    )
    recruiter_client, _ = await authed_client("dash-empty-recruiter@example.com")
    # Recruiter opens the chat widget (creates a session) but never actually sends a message.
    await recruiter_client.post(f"/api/v1/public/chat/{slug}/sessions")

    owner_client, _ = await authed_client("dash-empty-owner@example.com")
    response = await owner_client.get("/api/v1/dashboard/conversations", headers=owner_headers)

    assert response.status_code == 200
    assert response.json() == []


async def test_get_conversation_returns_full_message_history(
    set_fake_llm: Callable[[str], None],
) -> None:
    _, owner_headers, slug = await publish_full_profile(
        "dash-detail-owner@example.com", set_fake_llm, "dash-2"
    )
    await _send_recruiter_message(slug, "dash-detail-recruiter@example.com", "Tell me more.")

    owner_client, _ = await authed_client("dash-detail-owner@example.com")
    conversations = (
        await owner_client.get("/api/v1/dashboard/conversations", headers=owner_headers)
    ).json()
    conversation_id = conversations[0]["id"]

    response = await owner_client.get(
        f"/api/v1/dashboard/conversations/{conversation_id}", headers=owner_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "recruiter"
    assert body["messages"][0]["content"] == "Tell me more."
    assert body["messages"][1]["role"] == "assistant"


async def test_mark_conversation_reviewed_sets_flag(set_fake_llm: Callable[[str], None]) -> None:
    _, owner_headers, slug = await publish_full_profile(
        "dash-review-owner@example.com", set_fake_llm, "dash-3"
    )
    await _send_recruiter_message(slug, "dash-review-recruiter@example.com", "Hi!")

    owner_client, _ = await authed_client("dash-review-owner@example.com")
    conversations = (
        await owner_client.get("/api/v1/dashboard/conversations", headers=owner_headers)
    ).json()
    conversation_id = conversations[0]["id"]

    response = await owner_client.post(
        f"/api/v1/dashboard/conversations/{conversation_id}/mark-reviewed", headers=owner_headers
    )

    assert response.status_code == 200
    assert response.json()["reviewed_by_candidate"] is True


async def test_conversation_not_visible_to_other_candidate(
    set_fake_llm: Callable[[str], None],
) -> None:
    _, _owner_headers, slug = await publish_full_profile(
        "dash-isolation-owner@example.com", set_fake_llm, "dash-4"
    )
    await _send_recruiter_message(slug, "dash-isolation-recruiter@example.com", "Hi!")

    owner_client, owner_headers = await authed_client("dash-isolation-owner@example.com")
    conversations = (
        await owner_client.get("/api/v1/dashboard/conversations", headers=owner_headers)
    ).json()
    conversation_id = conversations[0]["id"]

    other_client, other_headers = await authed_client("dash-isolation-other@example.com")
    response = await other_client.get(
        f"/api/v1/dashboard/conversations/{conversation_id}", headers=other_headers
    )

    assert response.status_code == 404
    other_list = await other_client.get("/api/v1/dashboard/conversations", headers=other_headers)
    assert other_list.json() == []


async def test_get_unknown_conversation_returns_404() -> None:
    client, headers = await authed_client("dash-unknown-owner@example.com")
    response = await client.get(f"/api/v1/dashboard/conversations/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


async def test_list_conversations_requires_auth() -> None:
    client, _ = await authed_client("dash-noauth@example.com")
    response = await client.get("/api/v1/dashboard/conversations")
    assert response.status_code == 401
