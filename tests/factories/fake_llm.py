from chatfolio.config.settings import LLMTask
from chatfolio.llm.base import LLMCompletion, LLMProvider, Message


class FakeLLMProvider:
    def __init__(self, response: str, tokens_used: int = 0) -> None:
        self._response = response
        self._tokens_used = tokens_used

    async def complete(
        self, *, system: str, messages: list[Message], json_mode: bool = False
    ) -> LLMCompletion:
        return LLMCompletion(content=self._response, tokens_used=self._tokens_used)


class FakeLLMFactory:
    """Pass a single str for one response regardless of task, or a dict[LLMTask, str] when a
    test drives a multi-step pipeline (e.g. chat: one response for INTENT, another for CHAT).
    `tokens_used` defaults to 0 — pass a positive value only in a test that specifically asserts
    on token-usage tracking."""

    def __init__(self, response: str | dict[LLMTask, str], tokens_used: int = 0) -> None:
        self._response = response
        self._tokens_used = tokens_used

    def for_task(self, task: LLMTask) -> LLMProvider:
        text = self._response[task] if isinstance(self._response, dict) else self._response
        return FakeLLMProvider(text, self._tokens_used)
