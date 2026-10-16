from chatfolio.llm.providers.openai_compatible import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    """OpenAI-compatible chat completions API. https://api-docs.deepseek.com"""

    def __init__(self, *, api_key: str, model: str, base_url: str, timeout: float = 60.0) -> None:
        super().__init__(api_key=api_key, model=model, base_url=base_url, timeout=timeout)
