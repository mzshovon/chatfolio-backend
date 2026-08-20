from chatfolio.config.settings import LLMProviderName, LLMSettings, LLMTask
from chatfolio.llm.base import LLMProvider
from chatfolio.llm.providers.deepseek import DeepSeekProvider


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
            return DeepSeekProvider(api_key=api_key.get_secret_value())

        raise NotImplementedError(f"LLM provider {provider_name!r} is not implemented yet.")
