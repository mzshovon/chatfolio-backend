import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from chatfolio.core.exceptions import NotFoundError
from chatfolio.models.chat import ChatMessage, ChatSession
from chatfolio.models.chatfolio import PortfolioVisit
from chatfolio.models.profile import DEFAULT_AI_TOKENS_MONTHLY_QUOTA
from chatfolio.models.user import User
from chatfolio.services.portfolio_service import PortfolioService
from chatfolio.services.profile_service import ProfileService

DEFAULT_PAGE_SIZE = 20
ANALYTICS_WINDOW_DAYS = 30


class DashboardAnalytics:
    def __init__(
        self,
        *,
        portfolio_visitors_total: int,
        portfolio_visitors_delta_pct: int | None,
        ai_tokens_used: int,
        ai_tokens_monthly_quota: int,
    ) -> None:
        self.portfolio_visitors_total = portfolio_visitors_total
        self.portfolio_visitors_delta_pct = portfolio_visitors_delta_pct
        self.ai_tokens_used = ai_tokens_used
        self.ai_tokens_monthly_quota = ai_tokens_monthly_quota


class DashboardService:
    """Candidate-facing read access to their own recruiter conversations. Every query is scoped
    through the caller's own chatfolio — a candidate can never see another candidate's sessions,
    even by guessing a session id (get_conversation filters on chatfolio_id, not just session id).
    """

    def __init__(
        self,
        session: AsyncSession,
        portfolio_service: PortfolioService,
        profile_service: ProfileService,
    ) -> None:
        self._session = session
        self._portfolio_service = portfolio_service
        self._profile_service = profile_service

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

    async def get_analytics(self, user: User) -> DashboardAnalytics:
        chatfolio = await self._portfolio_service.get_or_create_for_user(user)
        profile = await self._profile_service.get_or_create_for_user(user)

        now = datetime.now(UTC)
        period_start = now - timedelta(days=ANALYTICS_WINDOW_DAYS)
        previous_period_start = now - timedelta(days=2 * ANALYTICS_WINDOW_DAYS)

        total = await self._count_visits(chatfolio.id)
        current_period = await self._count_visits(chatfolio.id, since=period_start)
        previous_period = await self._count_visits(
            chatfolio.id, since=previous_period_start, before=period_start
        )
        # No prior-period data to compare against yet (a brand new or just-published page) —
        # a delta against zero is meaningless, not "-100%" or "+inf", so report it as absent.
        delta_pct = (
            round((current_period - previous_period) / previous_period * 100)
            if previous_period > 0
            else None
        )

        return DashboardAnalytics(
            portfolio_visitors_total=total,
            portfolio_visitors_delta_pct=delta_pct,
            ai_tokens_used=profile.ai_tokens_used,
            ai_tokens_monthly_quota=profile.usage_limits.get(
                "ai_tokens_monthly_quota", DEFAULT_AI_TOKENS_MONTHLY_QUOTA
            ),
        )

    async def _count_visits(
        self,
        chatfolio_id: uuid.UUID,
        *,
        since: datetime | None = None,
        before: datetime | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(PortfolioVisit).where(
            PortfolioVisit.chatfolio_id == chatfolio_id
        )
        if since is not None:
            stmt = stmt.where(PortfolioVisit.visited_at >= since)
        if before is not None:
            stmt = stmt.where(PortfolioVisit.visited_at < before)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

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
