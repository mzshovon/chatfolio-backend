import httpx

from chatfolio.llm.base import LLMCompletion, Message


class OpenAICompatibleProvider:
    """Any API that speaks OpenAI's `/chat/completions` shape — OpenAI itself, DeepSeek, and
    Gemini's OpenAI-compatibility endpoint. One class instead of one per vendor: the three differ
    only in `base_url`, `model` and the API key.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float = 60.0,
        supports_json_mode: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        # Always end with "/" so the relative "chat/completions" path joins under any path prefix
        # (…/v1, …/v1beta/openai) instead of replacing its last segment.
        self._base_url = base_url.rstrip("/") + "/"
        self._timeout = timeout
        self._supports_json_mode = supports_json_mode
        self._transport = transport  # tests inject a mock transport; None = real network

    async def complete(
        self, *, system: str, messages: list[Message], json_mode: bool = False
    ) -> LLMCompletion:
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, *messages],
        }
        # Only ask for JSON mode where the model is known to support it; a model that rejects the
        # parameter would otherwise fail every call instead of just returning plain text.
        if json_mode and self._supports_json_mode:
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout, transport=self._transport
        ) as client:
            response = await client.post(
                "chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            response.raise_for_status()
            data = response.json()
            content: str = data["choices"][0]["message"]["content"]
            tokens_used: int = (data.get("usage") or {}).get("total_tokens", 0)
            return LLMCompletion(content=content, tokens_used=tokens_used)
