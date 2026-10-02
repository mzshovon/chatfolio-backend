import uuid

from fastapi import APIRouter, Request, status

from chatfolio.api.deps import (
    DbSessionDep,
    EmbedTextsDep,
    LLMFactoryDep,
    SettingsDep,
    VectorStoreDep,
)
from chatfolio.config.settings import get_settings
from chatfolio.core.rate_limit import limiter
from chatfolio.repositories.meeting_repository import MeetingRepository
from chatfolio.schemas.chat import ChatMessageResponse, SendMessageRequest, StartSessionResponse
from chatfolio.schemas.meeting import MeetingRequest, PublicMeetingRequest, PublicMeetingResponse
from chatfolio.services.chat_service import ChatService
from chatfolio.services.google_oauth_client import GoogleOAuthClient
from chatfolio.services.meeting_service import MeetingSettingsService
from chatfolio.services.rag_service import RAGService

router = APIRouter(prefix="/public/chat", tags=["public-chat"])


def _service(
    session: DbSessionDep,
    vector_store: VectorStoreDep,
    embed_texts: EmbedTextsDep,
    llm_factory: LLMFactoryDep,
) -> ChatService:
    rag = RAGService(
        vector_store, embed_texts, llm_factory, get_settings().llm.retrieval_similarity_threshold
    )
    return ChatService(session, rag)


@router.post("/{slug}/sessions", response_model=StartSessionResponse)
@limiter.limit("10/minute")
async def start_chat_session(
    request: Request,
    slug: str,
    session: DbSessionDep,
    vector_store: VectorStoreDep,
    embed_texts: EmbedTextsDep,
    llm_factory: LLMFactoryDep,
) -> StartSessionResponse:
    service = _service(session, vector_store, embed_texts, llm_factory)
    chat_session = await service.start_session(slug)
    return StartSessionResponse(session_id=chat_session.id)


@router.post("/sessions/{session_id}/messages", response_model=ChatMessageResponse)
@limiter.limit("15/minute")
async def send_chat_message(
    request: Request,
    session_id: uuid.UUID,
    payload: SendMessageRequest,
    session: DbSessionDep,
    vector_store: VectorStoreDep,
    embed_texts: EmbedTextsDep,
    llm_factory: LLMFactoryDep,
) -> ChatMessageResponse:
    reply = await _service(session, vector_store, embed_texts, llm_factory).send_message(
        session_id, payload.content
    )
    return ChatMessageResponse.model_validate(reply)


@router.post(
    "/sessions/{session_id}/meetings",
    response_model=PublicMeetingResponse,
    status_code=status.HTTP_201_CREATED,
)
# Tight on purpose: each call makes Google email an invite from the candidate's own account.
@limiter.limit("3/hour")
async def request_meeting(
    request: Request,
    session_id: uuid.UUID,
    payload: PublicMeetingRequest,
    session: DbSessionDep,
    settings: SettingsDep,
) -> PublicMeetingResponse:
    service = MeetingSettingsService(
        session,
        MeetingRepository(session),
        GoogleOAuthClient(settings.google_oauth),
        settings.security,
    )
    who = f" with {payload.attendee_name}" if payload.attendee_name else ""
    description = "Interview scheduled through Chatfolio."
    if payload.message:
        description += f"\n\nMessage from the recruiter: {payload.message}"
    meeting = await service.schedule_public_meeting(
        session_id,
        MeetingRequest(
            attendee_email=payload.attendee_email,
            start=payload.start,
            duration_minutes=payload.duration_minutes,
            timezone=payload.timezone,
            title=f"Interview via Chatfolio{who}",
            description=description,
            request_id=payload.request_id,
        ),
        expected_user_id=payload.user_id,
    )
    return PublicMeetingResponse(
        meet_link=meeting.meet_link,
        title=meeting.title,
        start=meeting.start,
        end=meeting.end,
        timezone=meeting.timezone,
    )
