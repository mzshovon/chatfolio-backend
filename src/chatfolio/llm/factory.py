from chatfolio.config.settings import LLMProviderName, LLMSettings, LLMTask
from chatfolio.llm.base import LLMProvider
from chatfolio.llm.providers.deepseek import DeepSeekProvider

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


class LLMProviderFactory:
    """Resolves the configured provider for a task (see LLMSettings.provider_for).

    Only DeepSeek is implemented today, matching the pilot's default per Requirement.md.
    GPT/Gemini/Claude/Grok/OpenRouter are registered as config values already (LLMProviderName)
    but intentionally have no provider class yet — add one under llm/providers/ and a branch
    here when a task is actually configured to use it.
    """

    def __init__(self, settings: LLMSettings) -> None:
        self._settings = settings

    def for_task(self, task: LLMTask) -> LLMProvider:
        provider_name = self._settings.provider_for(task)

        if provider_name == LLMProviderName.DEEPSEEK:
            api_key = self._settings.deepseek_api_key
            # An env var set to an empty string loads as SecretStr(""), not None — both mean
            # "not configured" and must be rejected the same way, or requests go out with a
            # blank Bearer token instead of failing cleanly.
            if api_key is None or not api_key.get_secret_value():
                raise RuntimeError("LLM_DEEPSEEK_API_KEY is not configured.")
            return DeepSeekProvider(
                api_key=api_key.get_secret_value(), timeout=_TASK_TIMEOUT_SECONDS[task]
            )

        raise NotImplementedError(f"LLM provider {provider_name!r} is not implemented yet.")
