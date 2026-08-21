from chatfolio.config.settings import LLMTask
from chatfolio.llm.base import LLMProvider, Message


class FakeLLMProvider:
    def __init__(self, response: str) -> None:
        self._response = response

    async def complete(
        self, *, system: str, messages: list[Message], json_mode: bool = False
    ) -> str:
        return self._response


class FakeLLMFactory:
    """Pass a single str for one response regardless of task, or a dict[LLMTask, str] when a
    test drives a multi-step pipeline (e.g. chat: one response for INTENT, another for CHAT)."""

    def __init__(self, response: str | dict[LLMTask, str]) -> None:
        self._response = response

    def for_task(self, task: LLMTask) -> LLMProvider:
        text = self._response[task] if isinstance(self._response, dict) else self._response
        return FakeLLMProvider(text)
