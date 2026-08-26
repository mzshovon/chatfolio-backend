import asyncio
import json
import uuid
from collections.abc import Callable

import structlog

from chatfolio.config.settings import LLMTask
from chatfolio.core.exceptions import ServiceUnavailableError
from chatfolio.llm.base import LLMFactory, Message
from chatfolio.llm.prompts.chat import (
    CHAT_FALLBACK_RESPONSE,
    CHAT_SYSTEM_PROMPT_TEMPLATE,
    INTENT_CLASSIFICATION_SYSTEM_PROMPT,
)
from chatfolio.models.chat import INFORMATIONAL_INTENTS, RecruiterIntent
from chatfolio.services.embedding_service import EMBEDDING_COLLECTION
from chatfolio.vectorstore.base import QueryMatch, VectorStore

logger = structlog.get_logger(__name__)

RECRUITER_CONTEXT_FIELDS = (
    "name",
    "company",
    "role",
    "required_skills",
    "experience_expectation",
    "location_pref",
    "timeline",
)


class RAGService:
    """Intent classification, retrieval, and grounded response generation.

    Guardrail design: for intents that ask about a specific fact (skills/projects/experience/
    education/availability), if retrieval finds nothing above the similarity threshold, the
    fallback is returned WITHOUT calling the generation LLM at all — the strongest possible
    guardrail against fabrication, since there's no model call that could hallucinate. For
    conversational intents (greetings, role-fit chat, contact requests), generation still runs
    using the candidate's already-public-approved intro/summary as a safe baseline, even with
    no retrieval hits — small talk shouldn't need to match a resume line.
    """

    def __init__(
        self,
        vector_store: VectorStore,
        embed_texts: Callable[[list[str]], list[list[float]]],
        llm_factory: LLMFactory,
        similarity_threshold: float,
    ) -> None:
        self._vector_store = vector_store
        self._embed_texts = embed_texts
        self._llm_factory = llm_factory
        self._threshold = similarity_threshold

    async def classify_and_extract(
        self, message: str
    ) -> tuple[RecruiterIntent, dict[str, str], int]:
        try:
            provider = self._llm_factory.for_task(LLMTask.INTENT)
            completion = await provider.complete(
                system=INTENT_CLASSIFICATION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": message}],
                json_mode=True,
            )
            data = json.loads(completion.content)
            intent = RecruiterIntent(data.get("intent", "unknown"))
        except Exception:
            logger.warning("chat.intent_classification_failed", exc_info=True)
            return RecruiterIntent.UNKNOWN, {}, 0

        raw_context = data.get("recruiter_context") or {}
        context = {
            field: raw_context[field]
            for field in RECRUITER_CONTEXT_FIELDS
            if isinstance(raw_context.get(field), str) and raw_context[field].strip()
        }
        return intent, context, completion.tokens_used

    async def retrieve(
        self, profile_id: uuid.UUID, message: str, n_results: int = 5
    ) -> list[QueryMatch]:
        embeddings = await asyncio.to_thread(self._embed_texts, [message])
        matches = await self._vector_store.query(
            collection=EMBEDDING_COLLECTION,
            query_embedding=embeddings[0],
            n_results=n_results,
            where={"profile_id": str(profile_id)},
        )
        return [match for match in matches if (1 - match["distance"]) >= self._threshold]

    async def generate_reply(
        self,
        *,
        session_id: uuid.UUID,
        full_name: str | None,
        intro: str | None,
        summary: str | None,
        retrieved: list[QueryMatch],
        history: list[Message],
        user_message: str,
        intent: RecruiterIntent,
    ) -> tuple[str, int]:
        if intent in INFORMATIONAL_INTENTS and not retrieved:
            return CHAT_FALLBACK_RESPONSE, 0

        if not retrieved:
            logger.warning(
                "chat.ungrounded_response", session_id=str(session_id), intent=intent.value
            )

        context_parts = []
        if intro:
            context_parts.append(f"Introduction: {intro}")
        if summary:
            context_parts.append(f"Career summary: {summary}")
        context_parts.extend(match["document"] for match in retrieved)
        context = "\n".join(context_parts) or "No profile information is available yet."

        system = CHAT_SYSTEM_PROMPT_TEMPLATE.format(
            full_name=full_name or "the candidate", context=context, fallback=CHAT_FALLBACK_RESPONSE
        )

        try:
            provider = self._llm_factory.for_task(LLMTask.CHAT)
            completion = await provider.complete(
                system=system, messages=[*history, {"role": "user", "content": user_message}]
            )
        except Exception as exc:
            logger.exception("chat.generation_failed", session_id=str(session_id))
            raise ServiceUnavailableError(
                "Chat is temporarily unavailable. Please try again shortly."
            ) from exc

        return completion.content.strip(), completion.tokens_used
