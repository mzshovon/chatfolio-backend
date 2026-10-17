import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from chatfolio.api.deps import get_llm_provider_factory, get_settings
from chatfolio.config.settings import LLMTask, SecuritySettings
from chatfolio.config.settings import get_settings as real_get_settings
from chatfolio.core.exceptions import ServiceUnavailableError
from chatfolio.core.redis import get_redis
from chatfolio.core.security import encrypt_secret
from chatfolio.db.session import get_sessionmaker
from chatfolio.entitlements.cache import EntitlementCache
from chatfolio.main import app
from chatfolio.models.billing import Subscription, SubscriptionStatus, UsageCounter
from chatfolio.models.chat import ChatSession
from chatfolio.models.meeting import GoogleCalendarConnection
from chatfolio.models.user import User
from chatfolio.services import chat_service
from chatfolio.services.google_oauth_client import GoogleOAuthClient
from chatfolio.services.meeting_service import MeetingSettingsService
from tests.factories.fake_llm import FakeLLMFactory
from tests.factories.plans import grant_plan, grant_premium, set_override, user_id_of
from tests.factories.publish_flow import authed_client, publish_full_profile

INTENT = json.dumps({"intent": "general_introduction", "recruiter_context": {}})


@pytest.fixture
def set_fake_llm() -> Callable[[str], None]:
    def _set(response: str) -> None:
        app.dependency_overrides[get_llm_provider_factory] = lambda: FakeLLMFactory(response)

    yield _set
    app.dependency_overrides.pop(get_llm_provider_factory, None)


@pytest.fixture(autouse=True)
def _no_chat_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_service, "COOLDOWN_SECONDS", 0.0)


async def _published_owner(
    email: str, slug: str, set_fake_llm: Callable[[str], None]
) -> tuple[AsyncClient, dict[str, str], uuid.UUID, str]:
    client, headers, _ = await publish_full_profile(email, set_fake_llm, slug)
    return client, headers, await user_id_of(client, headers), slug


async def _start_session(slug: str, recruiter_email: str) -> tuple[AsyncClient, str]:
    recruiter, _ = await authed_client(recruiter_email)
    response = await recruiter.post(f"/api/v1/public/chat/{slug}/sessions")
    assert response.status_code == 200, response.text
    return recruiter, response.json()["session_id"]


async def _say(
    recruiter: AsyncClient, session_id: str, text: str = "Hello!", *, tokens: int = 0
) -> Any:
    app.dependency_overrides[get_llm_provider_factory] = lambda: FakeLLMFactory(
        {LLMTask.INTENT: INTENT, LLMTask.CHAT: "Hi there."}, tokens_used=tokens
    )
    try:
        return await recruiter.post(
            f"/api/v1/public/chat/sessions/{session_id}/messages", json={"content": text}
        )
    finally:
        app.dependency_overrides.pop(get_llm_provider_factory, None)


# ---------- recruiter chat limit ----------


async def test_chat_limit_counts_first_messages_and_hides_the_plan_from_recruiters(
    set_fake_llm: Callable[[str], None],
) -> None:
    _, _, owner_id, slug = await _published_owner(
        "enf-chat-owner@example.com", "enf-chat", set_fake_llm
    )
    await set_override(owner_id, "recruiter_chats_limit", int_value=2)

    first, first_id = await _start_session(slug, "enf-r1@example.com")
    second, second_id = await _start_session(slug, "enf-r2@example.com")
    third, third_id = await _start_session(slug, "enf-r3@example.com")  # opening a chat is free

    assert (await _say(first, first_id)).status_code == 200
    assert (await _say(second, second_id)).status_code == 200
    blocked = await _say(third, third_id)

    assert blocked.status_code == 403
    body = blocked.json()
    assert body["code"] == "CHAT_UNAVAILABLE"
    # Neutral for the visitor: nothing about plans, limits or upgrades.
    assert set(body) == {"detail", "code"}
    assert not any(w in body["detail"].lower() for w in ("plan", "limit", "upgrade", "quota"))

    # Chats already in progress keep working: only a *new* chat costs a unit.
    assert (await _say(first, first_id, "And another thing")).status_code == 200


async def test_a_failed_reply_does_not_cost_the_owner_a_chat(
    set_fake_llm: Callable[[str], None],
) -> None:
    owner, owner_headers, owner_id, slug = await _published_owner(
        "enf-refund-owner@example.com", "enf-refund", set_fake_llm
    )
    await set_override(owner_id, "recruiter_chats_limit", int_value=1)
    recruiter, session_id = await _start_session(slug, "enf-refund-r@example.com")

    class Exploding:
        def for_task(self, task: LLMTask, *, owner_id: object = None) -> Any:
            if task == LLMTask.INTENT:
                return FakeLLMFactory(INTENT).for_task(task)
            raise RuntimeError("provider is down")

    app.dependency_overrides[get_llm_provider_factory] = lambda: Exploding()
    try:
        failed = await recruiter.post(
            f"/api/v1/public/chat/sessions/{session_id}/messages", json={"content": "Hi"}
        )
    finally:
        app.dependency_overrides.pop(get_llm_provider_factory, None)
    assert failed.status_code == 503

    assert (await _say(recruiter, session_id)).status_code == 200  # the unit was given back
    usage = (await owner.get("/api/v1/me/entitlements", headers=owner_headers)).json()
    assert usage["features"]["recruiter_chats_limit"]["used"] == 1


async def test_spent_ai_token_budget_stops_new_chats(set_fake_llm: Callable[[str], None]) -> None:
    _, _, owner_id, slug = await _published_owner(
        "enf-tok-owner@example.com", "enf-tok", set_fake_llm
    )
    await set_override(owner_id, "ai_tokens_limit", int_value=100)
    first, first_id = await _start_session(slug, "enf-tok-r1@example.com")
    second, second_id = await _start_session(slug, "enf-tok-r2@example.com")

    assert (await _say(first, first_id, tokens=80)).status_code == 200  # 160 tokens: overshoot ok
    blocked = await _say(second, second_id)

    assert blocked.status_code == 403 and blocked.json()["code"] == "CHAT_UNAVAILABLE"


async def test_messages_per_session_cap(set_fake_llm: Callable[[str], None]) -> None:
    _, _, owner_id, slug = await _published_owner(
        "enf-cap-owner@example.com", "enf-cap", set_fake_llm
    )
    await set_override(owner_id, "chat_messages_per_session", int_value=2)
    recruiter, session_id = await _start_session(slug, "enf-cap-r@example.com")

    assert (await _say(recruiter, session_id)).status_code == 200
    assert (await _say(recruiter, session_id)).status_code == 200
    capped = await _say(recruiter, session_id)

    assert capped.status_code == 403 and capped.json()["code"] == "CHAT_UNAVAILABLE"


# ---------- history window & insights ----------


async def _conversation_with_age(
    owner: AsyncClient, slug: str, recruiter_email: str, age_days: int
) -> str:
    recruiter, session_id = await _start_session(slug, recruiter_email)
    assert (await _say(recruiter, session_id)).status_code == 200
    async with get_sessionmaker()() as session:
        await session.execute(
            update(ChatSession)
            .where(ChatSession.id == uuid.UUID(session_id))
            .values(started_at=datetime.now(UTC) - timedelta(days=age_days))
        )
        await session.commit()
    return session_id


async def test_history_window_hides_old_conversations_everywhere_and_restores_on_upgrade(
    set_fake_llm: Callable[[str], None],
) -> None:
    owner, headers, owner_id, slug = await _published_owner(
        "enf-hist-owner@example.com", "enf-hist", set_fake_llm
    )
    recent = await _conversation_with_age(owner, slug, "enf-hist-r1@example.com", 1)
    old = await _conversation_with_age(owner, slug, "enf-hist-r2@example.com", 30)

    listed = (await owner.get("/api/v1/dashboard/conversations", headers=headers)).json()
    assert [c["id"] for c in listed] == [recent]  # free: 7-day window
    by_id = await owner.get(f"/api/v1/dashboard/conversations/{old}", headers=headers)
    assert by_id.status_code == 404  # guessing the id is not a way around the window
    assert (
        await owner.post(f"/api/v1/dashboard/conversations/{old}/mark-reviewed", headers=headers)
    ).status_code == 404

    await grant_plan(owner_id)  # upgrade: unlimited history, nothing was ever deleted
    listed = (await owner.get("/api/v1/dashboard/conversations", headers=headers)).json()
    assert {c["id"] for c in listed} == {recent, old}
    assert (
        await owner.get(f"/api/v1/dashboard/conversations/{old}", headers=headers)
    ).status_code == 200


async def test_recruiter_insights_are_stripped_on_free_and_shown_on_premium(
    set_fake_llm: Callable[[str], None],
) -> None:
    owner, headers, owner_id, slug = await _published_owner(
        "enf-ins-owner@example.com", "enf-ins", set_fake_llm
    )
    recruiter, session_id = await _start_session(slug, "enf-ins-r@example.com")
    app.dependency_overrides[get_llm_provider_factory] = lambda: FakeLLMFactory(
        {
            LLMTask.INTENT: json.dumps(
                {"intent": "general_introduction", "recruiter_context": {"company": "Acme"}}
            ),
            LLMTask.CHAT: "Hi.",
        }
    )
    try:
        await recruiter.post(
            f"/api/v1/public/chat/sessions/{session_id}/messages", json={"content": "Hello"}
        )
    finally:
        app.dependency_overrides.pop(get_llm_provider_factory, None)

    free = (await owner.get("/api/v1/dashboard/conversations", headers=headers)).json()[0]
    assert free["recruiter_metadata"] is None and free["recruiter_insights_locked"] is True
    detail = (
        await owner.get(f"/api/v1/dashboard/conversations/{session_id}", headers=headers)
    ).json()
    assert detail["recruiter_metadata"] is None

    await grant_plan(owner_id)
    paid = (await owner.get("/api/v1/dashboard/conversations", headers=headers)).json()[0]
    assert paid["recruiter_metadata"]["company"] == "Acme"
    assert paid["recruiter_insights_locked"] is False


# ---------- publishing ----------


async def test_publish_actions_are_counted_and_unpublish_does_not_refund(
    set_fake_llm: Callable[[str], None],
) -> None:
    owner, headers, owner_id, _ = await _published_owner(
        "enf-pub-owner@example.com", "enf-pub", set_fake_llm
    )  # helper's publish = action #1
    await set_override(owner_id, "publishes_limit", int_value=2)

    again = await owner.post("/api/v1/portfolio-settings/publish", headers=headers)
    assert again.status_code == 200  # already live: no state change, no unit

    assert (
        await owner.post("/api/v1/portfolio-settings/unpublish", headers=headers)
    ).status_code == 200
    assert (
        await owner.post("/api/v1/portfolio-settings/publish", headers=headers)
    ).status_code == 200  # #2
    await owner.post("/api/v1/portfolio-settings/unpublish", headers=headers)
    blocked = await owner.post("/api/v1/portfolio-settings/publish", headers=headers)  # would be #3

    assert blocked.status_code == 403
    assert blocked.json()["code"] == "LIMIT_REACHED"
    assert blocked.json()["feature"] == "publishes_limit"
    assert (blocked.json()["limit"], blocked.json()["used"]) == (2, 2)
    # And the page really stayed unpublished.
    state = (await owner.get("/api/v1/portfolio-settings", headers=headers)).json()
    assert state["is_published"] is False


async def test_validation_failure_does_not_consume_a_publish() -> None:
    client, headers = await authed_client("enf-pubval@example.com")
    uid = await user_id_of(client, headers)

    assert (
        await client.post("/api/v1/portfolio-settings/publish", headers=headers)
    ).status_code == 422

    usage = (await client.get("/api/v1/me/entitlements", headers=headers)).json()
    assert usage["features"]["publishes_limit"]["used"] == 0
    assert uid


# ---------- alias links ----------


async def test_free_plan_has_only_the_primary_link() -> None:
    client, headers = await authed_client("enf-alias-free@example.com")

    response = await client.post(
        "/api/v1/portfolio-settings/aliases", headers=headers, json={"slug": "my-extra"}
    )

    assert response.status_code == 403
    assert response.json()["code"] == "LIMIT_REACHED"
    assert response.json()["feature"] == "links_max"


async def test_premium_aliases_resolve_up_to_the_plan_and_lapse_on_downgrade(
    set_fake_llm: Callable[[str], None],
) -> None:
    owner, headers, owner_id, slug = await _published_owner(
        "enf-alias-owner@example.com", "enf-alias", set_fake_llm
    )
    await grant_plan(owner_id)  # links_max = 5 => primary + 4 aliases

    created = []
    for n in range(4):
        response = await owner.post(
            "/api/v1/portfolio-settings/aliases", headers=headers, json={"slug": f"alias-{n}-x"}
        )
        assert response.status_code == 201, response.text
        created.append(response.json())
    fifth = await owner.post(
        "/api/v1/portfolio-settings/aliases", headers=headers, json={"slug": "alias-5-x"}
    )
    assert fifth.status_code == 403 and fifth.json()["limit"] == 5

    # The alias serves the same page (canonical slug in the body) and takes chats.
    page = await owner.get("/api/v1/public/chatfolio/alias-0-x")
    assert page.status_code == 200 and page.json()["slug"] == slug
    assert (await owner.post("/api/v1/public/chat/alias-1-x/sessions")).status_code == 200

    # Deactivating frees a slot; reactivating needs room again.
    await owner.patch(
        f"/api/v1/portfolio-settings/aliases/{created[0]['id']}",
        headers=headers,
        json={"is_active": False},
    )
    assert (await owner.get("/api/v1/public/chatfolio/alias-0-x")).status_code == 404
    assert (
        await owner.post(
            "/api/v1/portfolio-settings/aliases", headers=headers, json={"slug": "alias-5-x"}
        )
    ).status_code == 201

    # Plan ends: aliases stop resolving but are kept (and flagged), the primary is unaffected.
    async with get_sessionmaker()() as session:
        for sub in (await session.execute(select(Subscription))).scalars():
            sub.status = SubscriptionStatus.CANCELLED
        await session.commit()
    await EntitlementCache(get_redis()).invalidate(owner_id)

    assert (await owner.get("/api/v1/public/chatfolio/alias-1-x")).status_code == 404
    assert (await owner.get(f"/api/v1/public/chatfolio/{slug}")).status_code == 200
    listed = (await owner.get("/api/v1/portfolio-settings/aliases", headers=headers)).json()
    assert len(listed) == 5 and not any(a["in_plan"] for a in listed)


async def test_alias_slug_must_be_unique_across_primaries_and_aliases(
    set_fake_llm: Callable[[str], None],
) -> None:
    owner, headers, owner_id, slug = await _published_owner(
        "enf-alias-uniq@example.com", "enf-uniq", set_fake_llm
    )
    other, other_headers, _, other_slug = await _published_owner(
        "enf-alias-uniq2@example.com", "enf-uniq-other", set_fake_llm
    )
    await grant_plan(owner_id)

    for taken in (slug, other_slug):
        response = await owner.post(
            "/api/v1/portfolio-settings/aliases", headers=headers, json={"slug": taken}
        )
        assert response.status_code == 409, taken

    await owner.post(
        "/api/v1/portfolio-settings/aliases", headers=headers, json={"slug": "mine-now"}
    )
    # ...and nobody can later claim it as a primary slug either.
    renamed = await other.patch(
        "/api/v1/portfolio-settings", headers=other_headers, json={"slug": "mine-now"}
    )
    assert renamed.status_code == 409


async def test_one_user_cannot_touch_anothers_alias(set_fake_llm: Callable[[str], None]) -> None:
    owner, headers, owner_id, _ = await _published_owner(
        "enf-alias-own@example.com", "enf-own", set_fake_llm
    )
    await grant_plan(owner_id)
    alias = (
        await owner.post(
            "/api/v1/portfolio-settings/aliases", headers=headers, json={"slug": "own-a"}
        )
    ).json()
    intruder, intruder_headers = await authed_client("enf-alias-intruder@example.com")

    for method in ("patch", "delete"):
        response = await getattr(intruder, method)(
            f"/api/v1/portfolio-settings/aliases/{alias['id']}",
            headers=intruder_headers,
            **({"json": {"is_active": False}} if method == "patch" else {}),
        )
        assert response.status_code == 404


# ---------- custom domains ----------


@pytest.fixture
def domains_enabled() -> Any:
    enabled = real_get_settings().model_copy(deep=True)
    enabled.features.enable_custom_domains = True
    app.dependency_overrides[get_settings] = lambda: enabled
    yield
    app.dependency_overrides.pop(get_settings, None)


async def test_domain_counts_follow_the_plan(domains_enabled: None) -> None:
    free, free_headers = await authed_client("enf-dom-free@example.com")
    assert (
        await free.post(
            "/api/v1/portfolio-settings/domains",
            headers=free_headers,
            json={"domain": "a.example.com"},
        )
    ).status_code == 403

    paid, paid_headers = await authed_client("enf-dom-paid@example.com")
    await grant_premium(paid, paid_headers)
    ids = []
    for n in range(5):
        response = await paid.post(
            "/api/v1/portfolio-settings/domains",
            headers=paid_headers,
            json={"domain": f"d{n}.example.com"},
        )
        assert response.status_code == 201
        ids.append(response.json()["id"])
    sixth = await paid.post(
        "/api/v1/portfolio-settings/domains",
        headers=paid_headers,
        json={"domain": "d6.example.com"},
    )
    assert sixth.status_code == 403 and sixth.json()["feature"] == "custom_domains_max"

    assert (
        len((await paid.get("/api/v1/portfolio-settings/domains", headers=paid_headers)).json())
        == 5
    )
    assert (
        await paid.delete(f"/api/v1/portfolio-settings/domains/{ids[0]}", headers=paid_headers)
    ).status_code == 204
    assert (
        await paid.post(
            "/api/v1/portfolio-settings/domains",
            headers=paid_headers,
            json={"domain": "d6.example.com"},
        )
    ).status_code == 201  # a freed slot is reusable


async def test_domain_ids_are_owner_scoped(domains_enabled: None) -> None:
    owner, headers = await authed_client("enf-dom-own@example.com")
    await grant_premium(owner, headers)
    domain = (
        await owner.post(
            "/api/v1/portfolio-settings/domains",
            headers=headers,
            json={"domain": "own.example.com"},
        )
    ).json()
    intruder, intruder_headers = await authed_client("enf-dom-intruder@example.com")

    response = await intruder.delete(
        f"/api/v1/portfolio-settings/domains/{domain['id']}", headers=intruder_headers
    )

    assert response.status_code == 404


# ---------- meetings ----------


async def _connect_google(user_id: uuid.UUID) -> None:
    async with get_sessionmaker()() as session:
        session.add(
            GoogleCalendarConnection(
                user_id=user_id,
                access_token_encrypted=encrypt_secret("ya29.fake", SecuritySettings()),
                access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
                granted_scopes="https://www.googleapis.com/auth/calendar.events",
            )
        )
        await session.commit()


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"fail": False, "created": 0}

    async def fake_create(self: Any, *, access_token: str, event: dict[str, Any]) -> dict[str, Any]:
        if state["fail"]:
            raise ServiceUnavailableError("google exploded")
        state["created"] += 1
        return {"id": f"evt{state['created']}", "hangoutLink": "https://meet.google.com/x"}

    monkeypatch.setattr(GoogleOAuthClient, "create_calendar_event", fake_create)
    return state


def _meeting_body(**extra: Any) -> dict[str, Any]:
    start = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0).isoformat()
    return {"attendee_email": "r@example.com", "start": start, **extra}


async def test_owner_meeting_count_gate_and_refund_on_google_failure(
    google: dict[str, Any],
) -> None:
    client, headers = await authed_client("enf-meet-owner@example.com")
    uid = await user_id_of(client, headers)
    await _connect_google(uid)
    await set_override(uid, "meetings_limit", int_value=1)
    url = "/api/v1/meeting-settings/meetings"

    google["fail"] = True
    assert (await client.post(url, headers=headers, json=_meeting_body())).status_code == 503
    google["fail"] = False  # the failed booking must not have burnt the allowance

    assert (await client.post(url, headers=headers, json=_meeting_body())).status_code == 201
    blocked = await client.post(url, headers=headers, json=_meeting_body())

    assert blocked.status_code == 403
    assert (
        blocked.json()["code"] == "LIMIT_REACHED" and blocked.json()["feature"] == "meetings_limit"
    )


async def test_calendar_booking_gate_is_enforced_on_every_route(google: dict[str, Any]) -> None:
    client, headers = await authed_client("enf-meet-gate@example.com")
    uid = await user_id_of(client, headers)
    await _connect_google(uid)
    await set_override(uid, "calendar_booking", bool_value=False)

    for method, url in (
        ("get", "/api/v1/meeting-settings/google/connect"),
        ("post", "/api/v1/meeting-settings/meetings"),
    ):
        response = await client.request(method.upper(), url, headers=headers, json=_meeting_body())
        assert response.status_code == 403, url
        assert response.json()["code"] == "FEATURE_NOT_IN_PLAN"
        assert response.json()["feature"] == "calendar_booking"
    assert google["created"] == 0


async def test_public_meeting_requests_use_the_owners_allowance_and_reveal_nothing(
    set_fake_llm: Callable[[str], None], google: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MeetingSettingsService, "_PUBLIC_REQUEST_COOLDOWN", timedelta(0))
    _, _, owner_id, slug = await _published_owner(
        "enf-pm-owner@example.com", "enf-pm", set_fake_llm
    )
    await _connect_google(owner_id)
    await set_override(owner_id, "meetings_limit", int_value=1)

    async def request(recruiter_email: str) -> Any:
        recruiter, session_id = await _start_session(slug, recruiter_email)
        return await recruiter.post(
            f"/api/v1/public/chat/sessions/{session_id}/meetings", json=_meeting_body()
        )

    assert (await request("enf-pm-r1@example.com")).status_code == 201
    blocked = await request("enf-pm-r2@example.com")

    assert blocked.status_code == 409  # same generic answer as "calendar not connected"
    assert "limit" not in blocked.text.lower() and "plan" not in blocked.text.lower()
    assert google["created"] == 1


# ---------- badge & usage meters ----------


async def test_badge_follows_the_owners_plan(set_fake_llm: Callable[[str], None]) -> None:
    owner, _, owner_id, slug = await _published_owner(
        "enf-badge-owner@example.com", "enf-badge", set_fake_llm
    )

    assert (await owner.get(f"/api/v1/public/chatfolio/{slug}")).json()["show_badge"] is True
    await grant_plan(owner_id)
    assert (await owner.get(f"/api/v1/public/chatfolio/{slug}")).json()["show_badge"] is False


async def test_entitlements_report_usage_and_remaining(set_fake_llm: Callable[[str], None]) -> None:
    owner, headers, owner_id, slug = await _published_owner(
        "enf-usage-owner@example.com", "enf-usage", set_fake_llm
    )
    recruiter, session_id = await _start_session(slug, "enf-usage-r@example.com")
    await _say(recruiter, session_id, tokens=50)

    features = (await owner.get("/api/v1/me/entitlements", headers=headers)).json()["features"]

    assert features["recruiter_chats_limit"]["used"] == 1
    assert features["recruiter_chats_limit"]["remaining"] == 19  # free: 20 / month
    assert (
        features["publishes_limit"]["used"] == 1 and features["publishes_limit"]["remaining"] == 2
    )
    assert features["ai_tokens_limit"]["used"] == 100  # intent + chat, 50 each
    assert features["links_max"]["used"] == 1 and features["links_max"]["remaining"] == 0
    assert features["recruiter_insights"]["used"] is None  # gates aren't countable
    await grant_plan(owner_id)
    paid = (await owner.get("/api/v1/me/entitlements", headers=headers)).json()["features"]
    assert paid["recruiter_chats_limit"]["unlimited"] is True
    assert paid["recruiter_chats_limit"]["remaining"] is None


async def test_deleting_the_user_cleans_up_their_counters(
    set_fake_llm: Callable[[str], None],
) -> None:
    _, _, owner_id, _ = await _published_owner("enf-del@example.com", "enf-del", set_fake_llm)
    async with get_sessionmaker()() as session:
        assert (
            await session.execute(select(UsageCounter).where(UsageCounter.user_id == owner_id))
        ).first() is not None
        user = await session.get(User, owner_id)
        await session.delete(user)
        await session.commit()
        assert (
            await session.execute(select(UsageCounter).where(UsageCounter.user_id == owner_id))
        ).first() is None
