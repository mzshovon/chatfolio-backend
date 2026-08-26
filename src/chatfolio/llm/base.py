from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypedDict

if TYPE_CHECKING:
    from chatfolio.config.settings import LLMTask


class Message(TypedDict):
    role: str
    content: str


@dataclass(frozen=True)
class LLMCompletion:
    content: str
    # 0 when a provider doesn't report usage (or none was ever called, e.g. a guardrail
    # short-circuit) — always a real number, never None, so callers can sum it unconditionally.
    tokens_used: int


class LLMProvider(Protocol):
    async def complete(
        self, *, system: str, messages: list[Message], json_mode: bool = False
    ) -> LLMCompletion: ...


class LLMFactory(Protocol):
    """Structural type for LLMProviderFactory — lets services/tests depend on 'something that
    resolves a provider per task' without importing the concrete factory (or a fake, in tests)."""

    def for_task(self, task: "LLMTask") -> LLMProvider: ...
