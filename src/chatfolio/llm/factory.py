import uuid

import structlog

from chatfolio.config.settings import LLMProviderName, LLMSettings, LLMTask
from chatfolio.llm.base import LLMCompletion, LLMProvider, Message
from chatfolio.llm.model_router import ModelRouter, ResolvedModel
from chatfolio.llm.providers.deepseek import DeepSeekProvider
from chatfolio.llm.providers.openai_compatible import OpenAICompatibleProvider

logger = structlog.get_logger(__name__)

# Tuned to how latency-sensitive each task actually is, not one flat number for everything.
# INTENT and CHAT sit in the live recruiter-chat request path — one after the other, so a slow
# INTENT call delays CHAT starting at all — so they stay tight; a real production incident on
# 2026-08-21 showed a 60s flat timeout meant a slow DeepSeek response left a recruiter staring
# at a stalled chat widget for a full minute before the (graceful, by design) UNKNOWN-intent
# fallback even kicked in. EXTRACTION and GENERATION are not in that path (EXTRACTION runs in
# the background CV-parsing job; GENERATION is the CMS's own request, not a public one) and can
# afford to wait longer for a good result rather than fail fast.
_TASK_TIMEOUT_SECONDS: dict[LLMTask, float] = {
    LLMTask.INTENT: 10.0,
    LLMTask.CHAT: 30.0,
    LLMTask.GENERATION: 45.0,
    LLMTask.EXTRACTION: 90.0,
    LLMTask.EMBEDDING: 30.0,  # unused by DeepSeek today, kept for when a provider adds it
}

# Tasks that answer on behalf of one candidate, so use that candidate's chosen model. INTENT stays
# on the system default on purpose (cheap classification — a premium user's model choice must not
# multiply its cost), and EXTRACTION/EMBEDDING are background system work.
_USER_ROUTED_TASKS = frozenset({LLMTask.CHAT, LLMTask.GENERATION})


class RoutedProvider:
    """Resolves the owner's model at call time (an async DB/cache lookup, which the synchronous
    `for_task` cannot do) and falls back to the system fallback model if the chosen provider
    errors or times out — a recruiter never sees a model-specific failure."""

    def __init__(
        self, factory: "LLMProviderFactory", router: ModelRouter, task: LLMTask, owner_id: uuid.UUID
    ) -> None:
        self._factory = factory
        self._router = router
        self._task = task
        self._owner_id = owner_id

    async def complete(
        self, *, system: str, messages: list[Message], json_mode: bool = False
    ) -> LLMCompletion:
        resolved = await self._router.resolve(self._owner_id)
        try:
            provider = self._factory.build(resolved, self._task)
            return await provider.complete(system=system, messages=messages, json_mode=json_mode)
        except Exception:
            if resolved.is_fallback:
                raise
            logger.warning(
                "llm.fell_back_to_default",
                task=self._task.value,
                failed_provider=resolved.provider.value,
                failed_model=resolved.model_id,
                exc_info=True,
            )
            fallback = await self._router.fallback()
            provider = self._factory.build(fallback, self._task)
            return await provider.complete(system=system, messages=messages, json_mode=json_mode)


class LLMProviderFactory:
    """Resolves a provider for a task.

    Without an `owner_id` (or for non-user tasks) the env-configured provider is used — DeepSeek
    only, matching the pilot's default per Requirement.md. With an `owner_id` on a user-routed
    task, the owner's model comes from the DB via the ModelRouter (see its docstring).
    """

    def __init__(self, settings: LLMSettings, router: ModelRouter | None = None) -> None:
        self._settings = settings
        self._router = router

    def for_task(self, task: LLMTask, *, owner_id: uuid.UUID | None = None) -> LLMProvider:
        if owner_id is not None and self._router is not None and task in _USER_ROUTED_TASKS:
            return RoutedProvider(self, self._router, task, owner_id)

        provider_name = self._settings.provider_for(task)
        if provider_name == LLMProviderName.DEEPSEEK:
            api_key = self._require_key(provider_name)
            return DeepSeekProvider(
                api_key=api_key,
                model=self._settings.deepseek_model,
                base_url=self._settings.deepseek_base_url,
                timeout=_TASK_TIMEOUT_SECONDS[task],
            )

        raise NotImplementedError(f"LLM provider {provider_name!r} is not implemented yet.")

    def build(self, model: ResolvedModel, task: LLMTask) -> LLMProvider:
        """A concrete provider for one DB model row."""
        base_url = self._settings.base_url_for(model.provider)
        if base_url is None:
            raise NotImplementedError(f"LLM provider {model.provider!r} is not implemented yet.")
        return OpenAICompatibleProvider(
            api_key=self._require_key(model.provider),
            model=model.model_id,
            base_url=base_url,
            timeout=_TASK_TIMEOUT_SECONDS[task],
            supports_json_mode=model.supports_json_mode,
        )

    def _require_key(self, provider: LLMProviderName) -> str:
        # An env var set to an empty string loads as SecretStr(""), not None — api_key_for treats
        # both as "not configured" so requests never go out with a blank Bearer token.
        key = self._settings.api_key_for(provider)
        if key is None:
            raise RuntimeError(f"No API key is configured for LLM provider {provider.value!r}.")
        return key
