import uuid

from fastapi import APIRouter, Query

from chatfolio.api.deps import CurrentUserDep, DbSessionDep
from chatfolio.models.chat import ChatSession, RecruiterMetadata
from chatfolio.repositories.profile_repository import ProfileRepository
from chatfolio.schemas.chat import ChatMessageResponse
from chatfolio.schemas.dashboard import (
    ConversationDetailResponse,
    ConversationSummaryResponse,
    DashboardAnalyticsResponse,
    RecruiterMetadataResponse,
)
from chatfolio.services.dashboard_service import DashboardService
from chatfolio.services.portfolio_service import PortfolioService
from chatfolio.services.profile_service import ProfileService

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _service(session: DbSessionDep) -> DashboardService:
    profile_service = ProfileService(ProfileRepository(session))
    portfolio_service = PortfolioService(session, profile_service)
    return DashboardService(session, portfolio_service, profile_service)


def _metadata_response(metadata: RecruiterMetadata | None) -> RecruiterMetadataResponse | None:
    return RecruiterMetadataResponse.model_validate(metadata) if metadata else None


def _to_summary(chat_session: ChatSession, message_count: int) -> ConversationSummaryResponse:
    return ConversationSummaryResponse(
        id=chat_session.id,
        started_at=chat_session.started_at,
        last_active_at=chat_session.last_active_at,
        is_flagged=chat_session.is_flagged,
        reviewed_by_candidate=chat_session.reviewed_by_candidate,
        message_count=message_count,
        recruiter_metadata=_metadata_response(chat_session.recruiter_metadata),
    )


def _to_detail(chat_session: ChatSession) -> ConversationDetailResponse:
    summary = _to_summary(chat_session, len(chat_session.messages))
    return ConversationDetailResponse(
        **summary.model_dump(),
        messages=[ChatMessageResponse.model_validate(m) for m in chat_session.messages],
    )


@router.get("/conversations", response_model=list[ConversationSummaryResponse])
async def list_conversations(
    current_user: CurrentUserDep,
    session: DbSessionDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[ConversationSummaryResponse]:
    pairs = await _service(session).list_conversations(current_user, limit=limit, offset=offset)
    return [_to_summary(chat_session, count) for chat_session, count in pairs]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: uuid.UUID, current_user: CurrentUserDep, session: DbSessionDep
) -> ConversationDetailResponse:
    chat_session = await _service(session).get_conversation(current_user, conversation_id)
    return _to_detail(chat_session)


@router.post(
    "/conversations/{conversation_id}/mark-reviewed", response_model=ConversationSummaryResponse
)
async def mark_conversation_reviewed(
    conversation_id: uuid.UUID, current_user: CurrentUserDep, session: DbSessionDep
) -> ConversationSummaryResponse:
    chat_session = await _service(session).mark_reviewed(current_user, conversation_id)
    return _to_summary(chat_session, len(chat_session.messages))


@router.get("/analytics", response_model=DashboardAnalyticsResponse)
async def get_analytics(
    current_user: CurrentUserDep, session: DbSessionDep
) -> DashboardAnalyticsResponse:
    analytics = await _service(session).get_analytics(current_user)
    return DashboardAnalyticsResponse(
        portfolio_visitors_total=analytics.portfolio_visitors_total,
        portfolio_visitors_delta_pct=analytics.portfolio_visitors_delta_pct,
        ai_tokens_used=analytics.ai_tokens_used,
        ai_tokens_monthly_quota=analytics.ai_tokens_monthly_quota,
    )
