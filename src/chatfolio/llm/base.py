from typing import TYPE_CHECKING, Protocol, TypedDict

if TYPE_CHECKING:
    from chatfolio.config.settings import LLMTask


class Message(TypedDict):
    role: str
    content: str


class LLMProvider(Protocol):
    async def complete(
        self, *, system: str, messages: list[Message], json_mode: bool = False
    ) -> str: ...


class LLMFactory(Protocol):
    """Structural type for LLMProviderFactory — lets services/tests depend on 'something that
    resolves a provider per task' without importing the concrete factory (or a fake, in tests)."""

    def for_task(self, task: "LLMTask") -> LLMProvider: ...
