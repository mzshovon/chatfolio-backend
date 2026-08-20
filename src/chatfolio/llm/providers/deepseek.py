import httpx

from chatfolio.llm.base import Message


class DeepSeekProvider:
    """OpenAI-compatible chat completions API. https://api-docs.deepseek.com"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url

    async def complete(
        self, *, system: str, messages: list[Message], json_mode: bool = False
    ) -> str:
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, *messages],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(base_url=self._base_url, timeout=60.0) as client:
            response = await client.post(
                "/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            response.raise_for_status()
            data = response.json()
            content: str = data["choices"][0]["message"]["content"]
            return content
