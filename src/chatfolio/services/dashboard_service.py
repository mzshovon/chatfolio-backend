import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from chatfolio.core.exceptions import NotFoundError
from chatfolio.models.chat import ChatMessage, ChatSession
from chatfolio.models.user import User
from chatfolio.services.portfolio_service import PortfolioService

DEFAULT_PAGE_SIZE = 20


class DashboardService:
    """Candidate-facing read access to their own recruiter conversations. Every query is scoped
    through the caller's own chatfolio — a candidate can never see another candidate's sessions,
    even by guessing a session id (get_conversation filters on chatfolio_id, not just session id).
    """

    def __init__(self, session: AsyncSession, portfolio_service: PortfolioService) -> None:
        self._session = session
        self._portfolio_service = portfolio_service

    async def list_conversations(
        self, user: User, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> list[tuple[ChatSession, int]]:
        chatfolio = await self._portfolio_service.get_or_create_for_user(user)
        result = await self._session.execute(
            select(ChatSession)
            .options(selectinload(ChatSession.recruiter_metadata))
            .where(ChatSession.chatfolio_id == chatfolio.id)
            .where(ChatSession.messages.any())
            .order_by(ChatSession.started_at.desc())
            .limit(limit)
            .offset(offset)
        )
        sessions = list(result.scalars().all())
        return await self._with_message_counts(sessions)

    async def get_conversation(self, user: User, session_id: uuid.UUID) -> ChatSession:
        chatfolio = await self._portfolio_service.get_or_create_for_user(user)
        result = await self._session.execute(
            select(ChatSession)
            .options(
                selectinload(ChatSession.recruiter_metadata), selectinload(ChatSession.messages)
            )
            .where(ChatSession.id == session_id, ChatSession.chatfolio_id == chatfolio.id)
        )
        chat_session = result.scalar_one_or_none()
        if chat_session is None:
            raise NotFoundError("Conversation not found.")
        return chat_session

    async def mark_reviewed(self, user: User, session_id: uuid.UUID) -> ChatSession:
        chat_session = await self.get_conversation(user, session_id)
        chat_session.reviewed_by_candidate = True
        await self._session.flush()
        return chat_session

    async def _with_message_counts(
        self, sessions: list[ChatSession]
    ) -> list[tuple[ChatSession, int]]:
        if not sessions:
            return []
        session_ids = [s.id for s in sessions]
        result = await self._session.execute(
            select(ChatMessage.session_id, func.count())
            .where(ChatMessage.session_id.in_(session_ids))
            .group_by(ChatMessage.session_id)
        )
        counts = dict(result.tuples().all())
        return [(s, counts.get(s.id, 0)) for s in sessions]
