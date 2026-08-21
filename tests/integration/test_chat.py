import json
import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from chatfolio.api.deps import get_llm_provider_factory, get_vector_store
from chatfolio.config.settings import LLMTask
from chatfolio.db.session import get_sessionmaker
from chatfolio.llm.base import LLMProvider
from chatfolio.main import app
from chatfolio.models.chat import ChatMessage, ChatSession, RecruiterMetadata
from tests.factories.fake_llm import FakeLLMFactory, FakeLLMProvider
from tests.factories.fake_vectorstore import FakeVectorStore
from tests.factories.publish_flow import authed_client, publish_full_profile


class TrackingLLMFactory:
    """Records which LLMTask.for_task() was actually called, so a test can assert the CHAT
    generation call was skipped entirely — not just that its result was ignored."""

    def __init__(self, responses: dict[LLMTask, str]) -> None:
        self._responses = responses
        self.calls: list[LLMTask] = []

    def for_task(self, task: LLMTask) -> LLMProvider:
        self.calls.append(task)
        return FakeLLMProvider(self._responses.get(task, ""))


def _intent_response(intent: str, **context: str) -> str:
    return json.dumps({"intent": intent, "recruiter_context": context})


@pytest.fixture
def set_fake_llm() -> Callable[[str], None]:
    def _set(response: str) -> None:
        app.dependency_overrides[get_llm_provider_factory] = lambda: FakeLLMFactory(response)

    yield _set
    app.dependency_overrides.pop(get_llm_provider_factory, None)


def _set_llm_factory(factory: object) -> None:
    app.dependency_overrides[get_llm_provider_factory] = lambda: factory


def _clear_llm_factory() -> None:
    app.dependency_overrides.pop(get_llm_provider_factory, None)


def _set_vector_store(store: FakeVectorStore) -> None:
    app.dependency_overrides[get_vector_store] = lambda: store


def _clear_vector_store() -> None:
    # Restore conftest's default fake, not remove the override entirely — popping it left later
    # tests (in this file and others) falling through to the real get_vector_store dependency,
    # which hits an actual Chroma server with fixed-dimension fake embeddings and 400s.
    app.dependency_overrides[get_vector_store] = lambda: FakeVectorStore()


async def _fetch_messages(session_id: uuid.UUID) -> list[ChatMessage]:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(
            select(ChatSession)
            .where(ChatSession.id == session_id)
            .options(selectinload(ChatSession.messages))
        )
        chat_session = result.scalar_one()
        return list(chat_session.messages)


async def _fetch_recruiter_metadata(session_id: uuid.UUID) -> RecruiterMetadata | None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(
            select(ChatSession)
            .where(ChatSession.id == session_id)
            .options(selectinload(ChatSession.recruiter_metadata))
        )
        chat_session = result.scalar_one()
        return chat_session.recruiter_metadata


async def _start_session(slug: str, recruiter_email: str) -> tuple[AsyncClient, str]:
    client, _ = await authed_client(recruiter_email)
    session_id = (await client.post(f"/v1/public/chat/{slug}/sessions")).json()["session_id"]
    return client, session_id


async def test_start_session_on_published_chatfolio_returns_session_id(
    set_fake_llm: Callable[[str], None],
) -> None:
    _, _headers, slug = await publish_full_profile(
        "chat-start-owner@example.com", set_fake_llm, "chat-1"
    )
    client, _ = await authed_client("chat-start-recruiter@example.com")

    response = await client.post(f"/v1/public/chat/{slug}/sessions")
    assert response.status_code == 200
    assert response.json()["session_id"]


async def test_start_session_on_unknown_slug_returns_404() -> None:
    client, _ = await authed_client("chat-unknown-recruiter@example.com")
    response = await client.post("/v1/public/chat/does-not-exist/sessions")
    assert response.status_code == 404


async def test_informational_intent_without_retrieval_skips_generation_and_falls_back(
    set_fake_llm: Callable[[str], None],
) -> None:
    _, _headers, slug = await publish_full_profile(
        "chat-fallback-owner@example.com", set_fake_llm, "chat-2"
    )
    client, session_id = await _start_session(slug, "chat-fallback-recruiter@example.com")

    factory = TrackingLLMFactory({LLMTask.INTENT: _intent_response("skill_inquiry")})
    _set_llm_factory(factory)
    _set_vector_store(FakeVectorStore(query_results=[]))
    try:
        response = await client.post(
            f"/v1/public/chat/sessions/{session_id}/messages",
            json={"content": "Do you know Rust?"},
        )
    finally:
        _clear_llm_factory()
        _clear_vector_store()

    assert response.status_code == 200
    body = response.json()
    assert "do not have that information" in body["content"]
    assert LLMTask.CHAT not in factory.calls


async def test_informational_intent_with_retrieval_generates_grounded_reply(
    set_fake_llm: Callable[[str], None],
) -> None:
    _, _headers, slug = await publish_full_profile(
        "chat-grounded-owner@example.com", set_fake_llm, "chat-3"
    )
    client, session_id = await _start_session(slug, "chat-grounded-recruiter@example.com")

    factory = TrackingLLMFactory(
        {
            LLMTask.INTENT: _intent_response("experience_inquiry"),
            LLMTask.CHAT: "I built the payments platform at Acme.",
        }
    )
    _set_llm_factory(factory)
    _set_vector_store(
        FakeVectorStore(
            query_results=[
                {
                    "id": "experience:x",
                    "document": "Engineer at Acme: built the payments platform.",
                    "distance": 0.1,
                    "metadata": {},
                }
            ]
        )
    )
    try:
        response = await client.post(
            f"/v1/public/chat/sessions/{session_id}/messages",
            json={"content": "Tell me about your experience."},
        )
    finally:
        _clear_llm_factory()
        _clear_vector_store()

    assert response.status_code == 200
    assert response.json()["content"] == "I built the payments platform at Acme."
    assert LLMTask.CHAT in factory.calls


async def test_general_introduction_generates_reply_even_without_retrieval(
    set_fake_llm: Callable[[str], None],
) -> None:
    _, _headers, slug = await publish_full_profile(
        "chat-hello-owner@example.com", set_fake_llm, "chat-4"
    )
    client, session_id = await _start_session(slug, "chat-hello-recruiter@example.com")

    factory = TrackingLLMFactory(
        {
            LLMTask.INTENT: _intent_response("general_introduction"),
            LLMTask.CHAT: "Hi there, thanks for reaching out!",
        }
    )
    _set_llm_factory(factory)
    _set_vector_store(FakeVectorStore(query_results=[]))
    try:
        response = await client.post(
            f"/v1/public/chat/sessions/{session_id}/messages", json={"content": "Hi!"}
        )
    finally:
        _clear_llm_factory()
        _clear_vector_store()

    assert response.status_code == 200
    assert response.json()["content"] == "Hi there, thanks for reaching out!"
    assert LLMTask.CHAT in factory.calls


async def test_messages_are_persisted_with_roles_and_intent(
    set_fake_llm: Callable[[str], None],
) -> None:
    _, _headers, slug = await publish_full_profile(
        "chat-persist-owner@example.com", set_fake_llm, "chat-5"
    )
    client, raw_session_id = await _start_session(slug, "chat-persist-recruiter@example.com")
    session_id = uuid.UUID(raw_session_id)

    factory = TrackingLLMFactory(
        {
            LLMTask.INTENT: _intent_response("contact_request"),
            LLMTask.CHAT: "You can reach me by email.",
        }
    )
    _set_llm_factory(factory)
    _set_vector_store(FakeVectorStore(query_results=[]))
    try:
        await client.post(
            f"/v1/public/chat/sessions/{session_id}/messages",
            json={"content": "How can I reach you?"},
        )
    finally:
        _clear_llm_factory()
        _clear_vector_store()

    messages = await _fetch_messages(session_id)
    assert [m.role.value for m in messages] == ["recruiter", "assistant"]
    assert messages[0].content == "How can I reach you?"
    assert messages[0].intent == "contact_request"
    assert messages[1].content == "You can reach me by email."


async def test_recruiter_context_merges_first_mention_wins(
    set_fake_llm: Callable[[str], None],
) -> None:
    _, _headers, slug = await publish_full_profile(
        "chat-context-owner@example.com", set_fake_llm, "chat-6"
    )
    client, raw_session_id = await _start_session(slug, "chat-context-recruiter@example.com")
    session_id = uuid.UUID(raw_session_id)

    _set_vector_store(FakeVectorStore(query_results=[]))
    try:
        _set_llm_factory(
            TrackingLLMFactory(
                {
                    LLMTask.INTENT: _intent_response("general_introduction", company="Acme Corp"),
                    LLMTask.CHAT: "Nice to meet you!",
                }
            )
        )
        await client.post(
            f"/v1/public/chat/sessions/{session_id}/messages",
            json={"content": "Hi, I'm hiring for Acme Corp."},
        )

        _set_llm_factory(
            TrackingLLMFactory(
                {
                    LLMTask.INTENT: _intent_response(
                        "general_introduction", company="A Different Co"
                    ),
                    LLMTask.CHAT: "Got it!",
                }
            )
        )
        await client.post(
            f"/v1/public/chat/sessions/{session_id}/messages",
            json={"content": "Actually, nevermind."},
        )
    finally:
        _clear_llm_factory()
        _clear_vector_store()

    metadata = await _fetch_recruiter_metadata(session_id)
    assert metadata is not None
    assert metadata.company == "Acme Corp"


async def test_rapid_messages_trigger_cooldown(set_fake_llm: Callable[[str], None]) -> None:
    _, _headers, slug = await publish_full_profile(
        "chat-cooldown-owner@example.com", set_fake_llm, "chat-7"
    )
    client, session_id = await _start_session(slug, "chat-cooldown-recruiter@example.com")

    _set_llm_factory(
        TrackingLLMFactory(
            {LLMTask.INTENT: _intent_response("general_introduction"), LLMTask.CHAT: "Hello!"}
        )
    )
    _set_vector_store(FakeVectorStore(query_results=[]))
    try:
        first = await client.post(
            f"/v1/public/chat/sessions/{session_id}/messages", json={"content": "Hi"}
        )
        second = await client.post(
            f"/v1/public/chat/sessions/{session_id}/messages", json={"content": "Hi again"}
        )
    finally:
        _clear_llm_factory()
        _clear_vector_store()

    assert first.status_code == 200
    assert second.status_code == 429


async def test_send_message_to_unknown_session_returns_404() -> None:
    client, _ = await authed_client("chat-unknown-session-recruiter@example.com")
    response = await client.post(
        f"/v1/public/chat/sessions/{uuid.uuid4()}/messages", json={"content": "hi"}
    )
    assert response.status_code == 404


async def test_send_message_after_unpublish_returns_404(
    set_fake_llm: Callable[[str], None],
) -> None:
    _, owner_headers, slug = await publish_full_profile(
        "chat-unpublish-owner@example.com", set_fake_llm, "chat-8"
    )
    owner_client, _ = await authed_client("chat-unpublish-owner@example.com")
    client, session_id = await _start_session(slug, "chat-unpublish-recruiter@example.com")

    await owner_client.post("/v1/portfolio-settings/unpublish", headers=owner_headers)

    response = await client.post(
        f"/v1/public/chat/sessions/{session_id}/messages", json={"content": "still there?"}
    )
    assert response.status_code == 404
