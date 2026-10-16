import uuid
from datetime import UTC, datetime, timedelta

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.core.exceptions import NotFoundError, TooManyRequestsError
from chatfolio.llm.base import Message
from chatfolio.models.chat import ChatMessage, ChatSession, MessageRole, RecruiterMetadata
from chatfolio.models.chatfolio import PublicChatfolio
from chatfolio.models.portfolio_section import PortfolioSection, SectionStatus, SectionType
from chatfolio.models.profile import CandidateProfile
from chatfolio.models.user import User
from chatfolio.services.rag_service import RAGService
from chatfolio.services.sms_service import SmsService, SmsTemplate, resolve_candidate_mobile

COOLDOWN_SECONDS = 2.0
RAPID_FIRE_FLAG_THRESHOLD = 5
# Last 5 messages (not 5 turns) — enough for the model to track the immediate thread (what was
# just discussed, pronouns/follow-ups) without so much history that an old, topically-adjacent
# answer (e.g. "cultural challenges") bleeds into a new, distinct question (e.g. "technical
# challenges"). CHAT_SYSTEM_PROMPT_TEMPLATE's own instruction is the primary guard against that;
# this window keeps the raw material it has to work with tight and recent.
HISTORY_WINDOW = 5


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

    async def send_message(
        self,
        session_id: uuid.UUID,
        content: str,
        *,
        sms_service: SmsService | None = None,
        background_tasks: BackgroundTasks | None = None,
    ) -> ChatMessage:
        chat_session = await self._get_session(session_id)
        is_first_message = chat_session.last_active_at is None
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

        intent, extracted_context, intent_tokens = await self._rag.classify_and_extract(content)
        recruiter_message.intent = intent.value
        await self._merge_recruiter_metadata(chat_session.id, extracted_context)

        if is_first_message and sms_service is not None and background_tasks is not None:
            await self._alert_candidate(
                profile, extracted_context, sms_service, background_tasks
            )

        sections = await self._approved_sections(profile.id)
        retrieved = await self._rag.retrieve(profile.id, content)
        reply_text, reply_tokens = await self._rag.generate_reply(
            session_id=chat_session.id,
            full_name=profile.full_name,
            intro=sections.get(SectionType.INTRO),
            summary=sections.get(SectionType.SUMMARY),
            location=profile.location,
            job_type=profile.job_type.value if profile.job_type else None,
            retrieved=retrieved,
            history=history,
            user_message=content,
            intent=intent,
            owner_id=profile.user_id,
        )
        profile.ai_tokens_used += intent_tokens + reply_tokens

        assistant_message = ChatMessage(
            session_id=chat_session.id,
            role=MessageRole.ASSISTANT,
            content=reply_text,
            # Mirrors the recruiter message's classified intent onto the reply that's actually
            # returned from this call — the frontend needs it here to key widget behavior off
            # the turn it just got back, not the one it already knows it sent.
            intent=intent.value,
        )
        self._session.add(assistant_message)
        chat_session.last_active_at = datetime.now(UTC)
        await self._session.flush()
        return assistant_message

    async def _alert_candidate(
        self,
        profile: CandidateProfile,
        extracted: dict[str, str],
        sms_service: SmsService,
        background_tasks: BackgroundTasks,
    ) -> None:
        """RECRUITER_ALERT: once per chat session, on the recruiter's first message. Name/company
        are whatever the LLM extracted from that message, with neutral fallbacks."""
        candidate = await self._session.get(User, profile.user_id)
        sms_service.queue(
            background_tasks,
            SmsTemplate.RECRUITER_ALERT,
            # Profile phone (what the portfolio shows, from the CV) first, account phone second.
            resolve_candidate_mobile(profile.phone, candidate.phone if candidate else None),
            name=profile.full_name or "there",
            recruiter_name=extracted.get("name") or "A recruiter",
            company=extracted.get("company") or "their company",
        )

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
