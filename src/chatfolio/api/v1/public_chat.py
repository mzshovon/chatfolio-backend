import uuid

from fastapi import APIRouter, Request

from chatfolio.api.deps import DbSessionDep, EmbedTextsDep, LLMFactoryDep, VectorStoreDep
from chatfolio.config.settings import get_settings
from chatfolio.core.rate_limit import limiter
from chatfolio.schemas.chat import ChatMessageResponse, SendMessageRequest, StartSessionResponse
from chatfolio.services.chat_service import ChatService
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
