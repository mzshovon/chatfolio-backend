import json
import uuid
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from chatfolio.api.deps import ModelRouterDep, SettingsDep, get_llm_provider_factory
from chatfolio.api.deps import get_settings as deps_get_settings
from chatfolio.config.settings import LLMProviderName, LLMSettings, LLMTask, Settings, get_settings
from chatfolio.core.exceptions import ServiceUnavailableError
from chatfolio.core.redis import get_redis
from chatfolio.db.session import get_sessionmaker
from chatfolio.entitlements.cache import EntitlementCache
from chatfolio.llm.base import LLMCompletion, Message
from chatfolio.llm.factory import LLMProviderFactory
from chatfolio.llm.model_router import ModelRouter
from chatfolio.llm.providers.deepseek import DeepSeekProvider
from chatfolio.llm.providers.openai_compatible import OpenAICompatibleProvider
from chatfolio.main import app
from chatfolio.models.billing import (
    LLMModel,
    LLMUsage,
    Plan,
    PlanModel,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
    UserModelPreference,
)
from chatfolio.services.entitlement_service import EntitlementService
from chatfolio.services.quota_service import QuotaService
from tests.factories.fake_llm import FakeLLMFactory, FakeLLMProvider
from tests.factories.publish_flow import authed_client, publish_full_profile

# ---------- helpers ----------


async def _user_id(client: httpx.AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me", headers=headers)).json()["id"])


async def _make_model(
    provider: LLMProviderName = LLMProviderName.OPENAI,
    model_id: str = "gpt-test",
    *,
    active: bool = True,
    plans: tuple[str, ...] = ("premium",),
) -> uuid.UUID:
    async with get_sessionmaker()() as session:
        model = LLMModel(
            provider=provider, model_id=model_id, display_name=model_id.upper(), is_active=active
        )
        session.add(model)
        await session.flush()
        for slug in plans:
            plan = (await session.execute(select(Plan).where(Plan.slug == slug))).scalar_one()
            session.add(PlanModel(plan_id=plan.id, model_id=model.id))
        await session.commit()
        return model.id


async def _grant_premium(user_id: uuid.UUID, *, days: int | None = 180) -> None:
    async with get_sessionmaker()() as session:
        plan = (await session.execute(select(Plan).where(Plan.slug == "premium"))).scalar_one()
        session.add(
            Subscription(
                user_id=user_id,
                plan_id=plan.id,
                status=SubscriptionStatus.ACTIVE,
                source=SubscriptionSource.ADMIN_GRANT,
                expires_at=datetime.now(UTC) + timedelta(days=days) if days else None,
            )
        )
        await session.commit()
    await EntitlementCache(get_redis()).invalidate(user_id)


async def _bump() -> None:
    await EntitlementCache(get_redis()).bump_catalog_version()


def _env_llm(
    default: LLMProviderName, *, models: dict[str, str] | None = None, **keys: str
) -> LLMSettings:
    """Settings as an operator would write them in .env: a default provider, its model, its key."""
    base = _llm_settings(**keys)
    return base.model_copy(
        update={
            "default_provider": default,
            **{f"{name}_model": value for name, value in (models or {}).items()},
        }
    )


def _llm_settings(**keys: str) -> LLMSettings:
    """Hermetic: ignores the developer's real .env (which may hold live API keys) so a test only
    ever sees the keys it passes in."""
    secrets: dict[str, SecretStr | None] = {
        "deepseek_api_key": None,
        "openai_api_key": None,
        "gemini_api_key": None,
    }
    secrets.update({f"{k}_api_key": SecretStr(v) for k, v in keys.items()})
    return LLMSettings(_env_file=None, **secrets)  # type: ignore[call-arg, arg-type]


async def _router(session: Any, **keys: str) -> ModelRouter:
    cache = EntitlementCache(get_redis())
    return ModelRouter(session, EntitlementService(session, cache), cache, _llm_settings(**keys))


@pytest.fixture
async def openai_configured() -> AsyncGenerator[None]:
    """Makes the API-level router see an OpenAI key (the real env has none in tests)."""
    settings: Settings = get_settings().model_copy(deep=True)
    settings.llm.openai_api_key = SecretStr("test-openai-key")
    app.dependency_overrides[deps_get_settings] = lambda: settings
    yield
    app.dependency_overrides.pop(deps_get_settings, None)


# ---------- the HTTP adapter ----------


def _capture(response: dict[str, Any]) -> tuple[list[httpx.Request], httpx.MockTransport]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=response)

    return seen, httpx.MockTransport(handler)


@pytest.mark.parametrize(
    ("provider", "expected_url"),
    [
        (LLMProviderName.DEEPSEEK, "https://api.deepseek.com/chat/completions"),
        (LLMProviderName.OPENAI, "https://api.openai.com/v1/chat/completions"),
        (
            LLMProviderName.GEMINI,
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        ),
    ],
)
async def test_each_provider_posts_to_its_own_chat_completions_url(
    provider: LLMProviderName, expected_url: str
) -> None:
    seen, transport = _capture(
        {"choices": [{"message": {"content": "hi"}}], "usage": {"total_tokens": 42}}
    )
    adapter = OpenAICompatibleProvider(
        api_key="sk-secret",
        model="m-1",
        base_url=_llm_settings().base_url_for(provider) or "",
        transport=transport,
    )

    completion = await adapter.complete(system="sys", messages=[{"role": "user", "content": "q"}])

    assert completion == LLMCompletion(content="hi", tokens_used=42)
    request = seen[0]
    assert str(request.url) == expected_url
    assert request.headers["authorization"] == "Bearer sk-secret"
    body = json.loads(request.content)
    assert body["model"] == "m-1"
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert "response_format" not in body


async def test_json_mode_is_only_sent_when_the_model_supports_it() -> None:
    for supports, expect in ((True, True), (False, False)):
        seen, transport = _capture({"choices": [{"message": {"content": "{}"}}]})
        adapter = OpenAICompatibleProvider(
            api_key="k",
            model="m",
            base_url="https://x.test/v1/",
            supports_json_mode=supports,
            transport=transport,
        )
        await adapter.complete(system="s", messages=[], json_mode=True)
        assert ("response_format" in json.loads(seen[0].content)) is expect


async def test_missing_usage_counts_as_zero_tokens() -> None:
    _, transport = _capture({"choices": [{"message": {"content": "ok"}}]})
    adapter = OpenAICompatibleProvider(
        api_key="k", model="m", base_url="https://x.test/v1/", transport=transport
    )
    assert (await adapter.complete(system="s", messages=[])).tokens_used == 0


# ---------- ModelRouter ----------


async def test_free_users_always_get_the_fallback_even_with_a_stored_choice() -> None:
    client, headers = await authed_client("mr-free@example.com")
    user_id = await _user_id(client, headers)
    gpt = await _make_model(plans=("free", "premium"))
    async with get_sessionmaker()() as session:
        session.add(UserModelPreference(user_id=user_id, model_id=gpt))
        await session.commit()

    async with get_sessionmaker()() as session:
        resolved = await (await _router(session, openai="k")).resolve(user_id)

    assert resolved.is_fallback and resolved.provider == LLMProviderName.DEEPSEEK


@pytest.mark.parametrize(
    "scenario", ["ok", "no_key", "inactive", "removed_from_plan", "subscription_expired"]
)
async def test_premium_choice_is_only_honoured_while_everything_still_allows_it(
    scenario: str,
) -> None:
    client, headers = await authed_client(f"mr-{scenario}@example.com")
    user_id = await _user_id(client, headers)
    gpt = await _make_model()
    await _grant_premium(user_id, days=-1 if scenario == "subscription_expired" else 180)
    async with get_sessionmaker()() as session:
        session.add(UserModelPreference(user_id=user_id, model_id=gpt))
        if scenario == "inactive":
            (await session.get(LLMModel, gpt)).is_active = False  # type: ignore[union-attr]
        if scenario == "removed_from_plan":
            await session.delete(await session.get(PlanModel, (await _premium_id(session), gpt)))
        await session.commit()
    await _bump()

    async with get_sessionmaker()() as session:
        keys = {} if scenario == "no_key" else {"openai": "k"}
        resolved = await (await _router(session, **keys)).resolve(user_id)

    assert resolved.is_fallback is (scenario != "ok")
    if scenario == "ok":
        assert resolved.model_id == "gpt-test" and resolved.provider == LLMProviderName.OPENAI


async def _premium_id(session: Any) -> uuid.UUID:
    return (await session.execute(select(Plan.id).where(Plan.slug == "premium"))).scalar_one()


# ---------- /me/models ----------


async def test_free_user_sees_models_locked_and_cannot_choose() -> None:
    client, headers = await authed_client("mm-free@example.com")
    gpt = await _make_model()

    listing = (await client.get("/api/v1/me/models", headers=headers)).json()

    assert listing["selection_enabled"] is False
    by_name = {m["model_id"]: m for m in listing["models"]}
    assert by_name["deepseek-chat"]["allowed"] is True and by_name["deepseek-chat"]["is_default"]
    assert by_name["gpt-test"]["allowed"] is False
    assert by_name["gpt-test"]["locked_reason"] == "plan"

    response = await client.put(
        "/api/v1/me/models/preference", headers=headers, json={"model_id": str(gpt)}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FEATURE_NOT_IN_PLAN"
    assert response.json()["feature"] == "model_selection"


async def test_premium_user_can_choose_and_clear_a_model(openai_configured: None) -> None:
    client, headers = await authed_client("mm-premium@example.com")
    await _grant_premium(await _user_id(client, headers))
    gpt = await _make_model()

    chosen = await client.put(
        "/api/v1/me/models/preference", headers=headers, json={"model_id": str(gpt)}
    )

    assert chosen.status_code == 200
    body = chosen.json()
    assert body["selection_enabled"] is True
    assert body["selected_model_id"] == str(gpt) == body["effective_model_id"]

    cleared = await client.put(
        "/api/v1/me/models/preference", headers=headers, json={"model_id": None}
    )
    assert cleared.json()["selected_model_id"] is None
    default = next(m for m in cleared.json()["models"] if m["is_default"])
    assert cleared.json()["effective_model_id"] == default["id"]


async def test_premium_user_cannot_pick_models_outside_their_plan_or_unknown_or_unconfigured(
    openai_configured: None,
) -> None:
    client, headers = await authed_client("mm-reject@example.com")
    await _grant_premium(await _user_id(client, headers))
    free_only = await _make_model(model_id="free-only", plans=("free",))
    no_key = await _make_model(LLMProviderName.GEMINI, "gemini-test")  # no Gemini key set

    async def put(model_id: object) -> httpx.Response:
        return await client.put(
            "/api/v1/me/models/preference", headers=headers, json={"model_id": str(model_id)}
        )

    assert (await put(free_only)).status_code == 403
    assert (await put(free_only)).json()["code"] == "MODEL_NOT_ALLOWED"
    assert (await put(no_key)).status_code == 403
    assert (await put(uuid.uuid4())).status_code == 404


async def test_models_endpoints_require_auth() -> None:
    client, _ = await authed_client("mm-anon@example.com")
    assert (await client.get("/api/v1/me/models")).status_code == 401
    assert (
        await client.put("/api/v1/me/models/preference", json={"model_id": None})
    ).status_code == 401


# ---------- RoutedProvider / factory ----------


class _Recording(FakeLLMProvider):
    def __init__(self, label: str, *, fail: bool = False) -> None:
        super().__init__(f"reply from {label}", tokens_used=7)
        self.label = label
        self._fail = fail

    async def complete(
        self, *, system: str, messages: list[Message], json_mode: bool = False
    ) -> LLMCompletion:
        if self._fail:
            raise TimeoutError(f"{self.label} timed out")
        return await super().complete(system=system, messages=messages, json_mode=json_mode)


class _TestFactory(LLMProviderFactory):
    """Real routing, fake network: `build` hands out recording providers instead."""

    def __init__(self, router: ModelRouter, failing: frozenset[str] = frozenset()) -> None:
        super().__init__(
            _llm_settings(openai="k", deepseek="k"),
            router,
            QuotaService(router._session, get_redis()),
        )
        self.built: list[str] = []
        self._failing = failing

    def build(self, model: Any, task: LLMTask) -> Any:
        self.built.append(model.model_id)
        return _Recording(model.model_id, fail=model.model_id in self._failing)


async def _premium_user_with_gpt(email: str) -> tuple[uuid.UUID, uuid.UUID]:
    client, headers = await authed_client(email)
    user_id = await _user_id(client, headers)
    await _grant_premium(user_id)
    gpt = await _make_model()
    async with get_sessionmaker()() as session:
        session.add(UserModelPreference(user_id=user_id, model_id=gpt))
        await session.commit()
    return user_id, gpt


async def test_routed_task_uses_the_owners_model() -> None:
    user_id, _ = await _premium_user_with_gpt("rp-ok@example.com")
    async with get_sessionmaker()() as session:
        factory = _TestFactory(await _router(session, openai="k"))
        completion = await factory.for_task(LLMTask.CHAT, owner_id=user_id).complete(
            system="s", messages=[]
        )

    assert completion.content == "reply from gpt-test"
    assert factory.built == ["gpt-test"]


async def test_failing_chosen_model_falls_back_to_the_default() -> None:
    user_id, _ = await _premium_user_with_gpt("rp-fail@example.com")
    async with get_sessionmaker()() as session:
        factory = _TestFactory(await _router(session, openai="k"), failing=frozenset({"gpt-test"}))
        completion = await factory.for_task(LLMTask.GENERATION, owner_id=user_id).complete(
            system="s", messages=[]
        )

    assert completion.content == "reply from deepseek-chat"
    assert factory.built == ["gpt-test", "deepseek-chat"]


async def test_failing_fallback_model_surfaces_the_error() -> None:
    client, headers = await authed_client("rp-fb-fail@example.com")
    user_id = await _user_id(client, headers)  # free => fallback, no second attempt
    async with get_sessionmaker()() as session:
        factory = _TestFactory(
            await _router(session, openai="k"), failing=frozenset({"deepseek-chat"})
        )
        with pytest.raises(TimeoutError):
            await factory.for_task(LLMTask.CHAT, owner_id=user_id).complete(system="s", messages=[])
    assert factory.built == ["deepseek-chat"]


async def test_intent_and_unowned_calls_are_not_user_routed() -> None:
    user_id, _ = await _premium_user_with_gpt("rp-intent@example.com")
    async with get_sessionmaker()() as session:
        factory = LLMProviderFactory(
            _llm_settings(deepseek="k", openai="k"), await _router(session, openai="k")
        )
        # INTENT with an owner, and CHAT without one: both take the env (DeepSeek) path.

        assert isinstance(factory.for_task(LLMTask.INTENT, owner_id=user_id), DeepSeekProvider)
        assert isinstance(factory.for_task(LLMTask.CHAT), DeepSeekProvider)


# ---------- end to end through the public chat ----------


@pytest.fixture
def set_fake_llm() -> Callable[[str], None]:
    def _set(response: str) -> None:
        app.dependency_overrides[get_llm_provider_factory] = lambda: FakeLLMFactory(response)

    yield _set
    app.dependency_overrides.pop(get_llm_provider_factory, None)


async def _chat_once(slug: str, recruiter_email: str, factory_for: Any) -> str:
    recruiter, _ = await authed_client(recruiter_email)
    session_id = (await recruiter.post(f"/api/v1/public/chat/{slug}/sessions")).json()["session_id"]

    # Real routing; only the network layer is faked. INTENT (not user-routed) gets a canned reply.
    def override(router: ModelRouterDep, settings: SettingsDep) -> Any:
        return factory_for(router)

    app.dependency_overrides[get_llm_provider_factory] = override
    try:
        response = await recruiter.post(
            f"/api/v1/public/chat/sessions/{session_id}/messages", json={"content": "Hello!"}
        )
    finally:
        app.dependency_overrides.pop(get_llm_provider_factory, None)
    assert response.status_code == 200, response.text
    return str(response.json()["content"])


class _ChatTestFactory(_TestFactory):
    def for_task(self, task: LLMTask, *, owner_id: uuid.UUID | None = None) -> Any:
        if task == LLMTask.INTENT:
            return FakeLLMProvider(json.dumps({"intent": "general_introduction"}))
        return super().for_task(task, owner_id=owner_id)


async def test_public_chat_is_answered_by_the_owners_chosen_model(
    set_fake_llm: Callable[[str], None], openai_configured: None
) -> None:
    owner, owner_headers, slug = await publish_full_profile(
        "e2e-owner@example.com", set_fake_llm, "e2e-premium"
    )
    owner_id = await _user_id(owner, owner_headers)
    await _grant_premium(owner_id)
    gpt = await _make_model()
    assert (
        await owner.put(
            "/api/v1/me/models/preference", headers=owner_headers, json={"model_id": str(gpt)}
        )
    ).status_code == 200

    built: list[_ChatTestFactory] = []

    def make(router: ModelRouter) -> _ChatTestFactory:
        built.append(_ChatTestFactory(router))
        return built[-1]

    reply = await _chat_once(slug, "e2e-recruiter@example.com", make)

    assert reply == "reply from gpt-test"
    assert built[0].built == ["gpt-test"]
    # Cost reporting: tokens/calls are recorded per user, per month, per model.
    async with get_sessionmaker()() as session:
        rows = (
            (await session.execute(select(LLMUsage).where(LLMUsage.user_id == owner_id)))
            .scalars()
            .all()
        )
    assert [(r.model_label, r.tokens, r.calls) for r in rows] == [("openai/gpt-test", 7, 1)]


async def test_public_chat_for_a_free_owner_uses_the_default_model(
    set_fake_llm: Callable[[str], None], openai_configured: None
) -> None:
    _, _, slug = await publish_full_profile("e2e-free@example.com", set_fake_llm, "e2e-free")
    await _make_model(plans=("free", "premium"))
    built: list[_ChatTestFactory] = []

    def make(router: ModelRouter) -> _ChatTestFactory:
        built.append(_ChatTestFactory(router))
        return built[-1]

    reply = await _chat_once(slug, "e2e-free-rec@example.com", make)

    assert reply == "reply from deepseek-chat"
    assert built[0].built == ["deepseek-chat"]


async def test_base_urls_and_deepseek_model_come_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_OPENAI_BASE_URL", "https://proxy.example.test/openai/v1")
    monkeypatch.setenv("LLM_DEEPSEEK_MODEL", "deepseek-custom")
    settings = LLMSettings(openai_api_key=SecretStr("k"), deepseek_api_key=SecretStr("k"))

    assert settings.base_url_for(LLMProviderName.OPENAI) == "https://proxy.example.test/openai/v1"
    assert settings.base_url_for(LLMProviderName.CLAUDE) is None
    seen, transport = _capture({"choices": [{"message": {"content": "x"}}]})
    adapter = OpenAICompatibleProvider(
        api_key="k",
        model="m",
        base_url=settings.base_url_for(LLMProviderName.OPENAI) or "",
        transport=transport,
    )
    await adapter.complete(system="s", messages=[])
    assert str(seen[0].url) == "https://proxy.example.test/openai/v1/chat/completions"

    env_provider = LLMProviderFactory(settings).for_task(LLMTask.INTENT)
    assert isinstance(env_provider, DeepSeekProvider)
    assert env_provider._model == "deepseek-custom"


# ---------- default / fallback come from the environment ----------


@pytest.mark.parametrize(
    ("provider", "key_name", "model", "host"),
    [
        (LLMProviderName.OPENAI, "openai", "gpt-env", "api.openai.com"),
        (LLMProviderName.GEMINI, "gemini", "gemini-env", "generativelanguage.googleapis.com"),
        (LLMProviderName.DEEPSEEK, "deepseek", "ds-env", "api.deepseek.com"),
    ],
)
def test_env_selected_provider_works_for_every_supported_provider(
    provider: LLMProviderName, key_name: str, model: str, host: str
) -> None:
    settings = _env_llm(provider, models={key_name: model}, **{key_name: "k"})

    adapter = LLMProviderFactory(settings).for_task(LLMTask.INTENT)

    assert isinstance(adapter, OpenAICompatibleProvider)
    assert adapter._model == model and host in adapter._base_url


def test_env_selected_provider_fails_with_actionable_messages() -> None:
    no_model = _env_llm(LLMProviderName.OPENAI, openai="k")
    with pytest.raises(RuntimeError, match="LLM_OPENAI_MODEL is not configured"):
        LLMProviderFactory(no_model).for_task(LLMTask.INTENT)

    no_key = _env_llm(LLMProviderName.GEMINI, models={"gemini": "g"})
    with pytest.raises(RuntimeError, match="No API key"):
        LLMProviderFactory(no_key).for_task(LLMTask.INTENT)

    unsupported = _env_llm(LLMProviderName.CLAUDE)
    with pytest.raises(NotImplementedError, match="no adapter"):
        LLMProviderFactory(unsupported).for_task(LLMTask.INTENT)


async def test_per_task_env_override_picks_a_different_provider() -> None:
    settings = _env_llm(
        LLMProviderName.DEEPSEEK, models={"openai": "gpt-env"}, deepseek="d", openai="o"
    ).model_copy(update={"provider_for_chat": LLMProviderName.OPENAI})

    factory = LLMProviderFactory(settings)

    assert factory.for_task(LLMTask.INTENT)._model == "deepseek-chat"  # type: ignore[attr-defined]
    assert factory.for_task(LLMTask.CHAT)._model == "gpt-env"  # type: ignore[attr-defined]


async def test_the_default_and_fallback_model_follow_the_env_provider() -> None:
    client, headers = await authed_client("env-default@example.com")
    user_id = await _user_id(client, headers)
    settings = _env_llm(LLMProviderName.OPENAI, models={"openai": "gpt-env"}, openai="k")

    async with get_sessionmaker()() as session:
        router = ModelRouter(
            session,
            EntitlementService(session, EntitlementCache(get_redis())),
            EntitlementCache(get_redis()),
            settings,
        )
        resolved = await router.resolve(user_id)  # a free user: no choice at all
        fallback = await router.fallback()

    assert (resolved.provider, resolved.model_id, resolved.is_fallback) == (
        LLMProviderName.OPENAI,
        "gpt-env",
        True,
    )
    assert fallback == resolved


async def test_misconfigured_default_fails_loudly_instead_of_guessing() -> None:
    client, headers = await authed_client("env-misconfig@example.com")
    user_id = await _user_id(client, headers)
    settings = _env_llm(LLMProviderName.OPENAI, openai="k")  # no LLM_OPENAI_MODEL

    async with get_sessionmaker()() as session:
        router = ModelRouter(
            session,
            EntitlementService(session, EntitlementCache(get_redis())),
            EntitlementCache(get_redis()),
            settings,
        )
        with pytest.raises(ServiceUnavailableError):
            await router.resolve(user_id)


async def test_a_failing_premium_choice_falls_back_to_the_env_default() -> None:
    user_id, _ = await _premium_user_with_gpt("env-fallback@example.com")
    settings = _env_llm(
        LLMProviderName.GEMINI, models={"gemini": "gemini-env"}, gemini="k", openai="k"
    )

    async with get_sessionmaker()() as session:
        cache = EntitlementCache(get_redis())
        router = ModelRouter(session, EntitlementService(session, cache), cache, settings)
        factory = _TestFactory(router, failing=frozenset({"gpt-test"}))
        completion = await factory.for_task(LLMTask.CHAT, owner_id=user_id).complete(
            system="s", messages=[]
        )

    assert completion.content == "reply from gemini-env"
    assert factory.built == ["gpt-test", "gemini-env"]


async def test_models_listing_leads_with_the_env_default_and_dedupes_its_db_twin(
    openai_configured: None,
) -> None:
    client, headers = await authed_client("env-listing@example.com")
    await _make_model(LLMProviderName.DEEPSEEK, "deepseek-chat", plans=("free", "premium"))
    await _make_model()

    listing = (await client.get("/api/v1/me/models", headers=headers)).json()

    names = [m["model_id"] for m in listing["models"]]
    assert names == ["deepseek-chat", "gpt-test"]  # the DB twin of the default isn't listed twice
    assert listing["models"][0]["is_default"] and listing["models"][0]["allowed"]
    assert listing["effective_model_id"] == listing["models"][0]["id"] == "default"
