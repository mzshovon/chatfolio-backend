import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.core.exceptions import NotFoundError, TooManyRequestsError
from chatfolio.llm.base import Message
from chatfolio.models.chat import ChatMessage, ChatSession, MessageRole, RecruiterMetadata
from chatfolio.models.chatfolio import PublicChatfolio
from chatfolio.models.portfolio_section import PortfolioSection, SectionStatus, SectionType
from chatfolio.models.profile import CandidateProfile
from chatfolio.services.rag_service import RAGService

COOLDOWN_SECONDS = 2.0
RAPID_FIRE_FLAG_THRESHOLD = 5
HISTORY_WINDOW = 6


class ChatService:
    def __init__(self, session: AsyncSession, rag_service: RAGService) -> None:
        self._session = session
        self._rag = rag_service

    async def start_session(self, slug: str) -> ChatSession:
        result = await self._session.execute(
            select(PublicChatfolio).where(
                PublicChatfolio.slug == slug, PublicChatfolio.is_published.is_(True)
            )
        )
        chatfolio = result.scalar_one_or_none()
        if chatfolio is None:
            raise NotFoundError("This Chatfolio is not available.")

        chat_session = ChatSession(chatfolio_id=chatfolio.id)
        self._session.add(chat_session)
        await self._session.flush()
        return chat_session

    async def send_message(self, session_id: uuid.UUID, content: str) -> ChatMessage:
        chat_session = await self._get_session(session_id)
        chatfolio = await self._session.get(PublicChatfolio, chat_session.chatfolio_id)
        if chatfolio is None or not chatfolio.is_published:
            raise NotFoundError("This Chatfolio is not available.")

        await self._enforce_cooldown(chat_session)

        profile = await self._session.get(CandidateProfile, chatfolio.profile_id)
        if profile is None:
            raise NotFoundError("This Chatfolio is not available.")

        history = await self._recent_history(chat_session.id)

        recruiter_message = ChatMessage(
            session_id=chat_session.id, role=MessageRole.RECRUITER, content=content
        )
        self._session.add(recruiter_message)
        await self._session.flush()

        intent, extracted_context = await self._rag.classify_and_extract(content)
        recruiter_message.intent = intent.value
        await self._merge_recruiter_metadata(chat_session.id, extracted_context)

        sections = await self._approved_sections(profile.id)
        retrieved = await self._rag.retrieve(profile.id, content)
        reply_text = await self._rag.generate_reply(
            session_id=chat_session.id,
            full_name=profile.full_name,
            intro=sections.get(SectionType.INTRO),
            summary=sections.get(SectionType.SUMMARY),
            retrieved=retrieved,
            history=history,
            user_message=content,
            intent=intent,
        )

        assistant_message = ChatMessage(
            session_id=chat_session.id, role=MessageRole.ASSISTANT, content=reply_text
        )
        self._session.add(assistant_message)
        chat_session.last_active_at = datetime.now(UTC)
        await self._session.flush()
        return assistant_message

    async def _get_session(self, session_id: uuid.UUID) -> ChatSession:
        chat_session = await self._session.get(ChatSession, session_id)
        if chat_session is None:
            raise NotFoundError("Chat session not found.")
        return chat_session

    async def _enforce_cooldown(self, chat_session: ChatSession) -> None:
        if chat_session.last_active_at is None:
            return  # first message of the session — nothing to be "rapid" relative to yet

        elapsed = datetime.now(UTC) - chat_session.last_active_at
        if elapsed < timedelta(seconds=COOLDOWN_SECONDS):
            chat_session.rapid_fire_count += 1
            if chat_session.rapid_fire_count >= RAPID_FIRE_FLAG_THRESHOLD:
                chat_session.is_flagged = True
            # Commit now, not just flush: raising below unwinds to get_db_session's
            # except-rollback, which would otherwise silently undo this increment on every
            # single violation — defeating the whole point of counting them.
            await self._session.commit()
            raise TooManyRequestsError()

    async def _recent_history(self, session_id: uuid.UUID) -> list[Message]:
        result = await self._session.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(HISTORY_WINDOW)
        )
        recent = list(reversed(result.scalars().all()))
        role_map = {MessageRole.RECRUITER: "user", MessageRole.ASSISTANT: "assistant"}
        return [{"role": role_map[m.role], "content": m.content} for m in recent]

    async def _approved_sections(self, profile_id: uuid.UUID) -> dict[SectionType, str]:
        result = await self._session.execute(
            select(PortfolioSection).where(
                PortfolioSection.profile_id == profile_id,
                PortfolioSection.status == SectionStatus.APPROVED,
            )
        )
        return {section.section_type: section.content for section in result.scalars().all()}

    async def _merge_recruiter_metadata(
        self, session_id: uuid.UUID, extracted: dict[str, str]
    ) -> None:
        if not extracted:
            return

        result = await self._session.execute(
            select(RecruiterMetadata).where(RecruiterMetadata.session_id == session_id)
        )
        metadata = result.scalar_one_or_none()
        if metadata is None:
            metadata = RecruiterMetadata(session_id=session_id)
            self._session.add(metadata)

        # First mention wins — don't let a later, possibly offhand remark overwrite context the
        # recruiter already gave earlier in the same conversation.
        for field, value in extracted.items():
            if getattr(metadata, field) is None:
                setattr(metadata, field, value)
