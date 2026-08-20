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
    def __init__(self, response: str) -> None:
        self._response = response

    def for_task(self, task: LLMTask) -> LLMProvider:
        return FakeLLMProvider(self._response)
