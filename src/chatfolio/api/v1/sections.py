import uuid

from fastapi import APIRouter, Request

from chatfolio.api.deps import (
    CurrentUserDep,
    DbSessionDep,
    JobQueueDep,
    LLMFactoryDep,
    VectorStoreDep,
)
from chatfolio.core.rate_limit import limiter
from chatfolio.repositories.profile_repository import ProfileRepository
from chatfolio.schemas.section import SectionResponse, SectionUpdateRequest
from chatfolio.services.embedding_service import EmbeddingService
from chatfolio.services.generation_service import GenerationService
from chatfolio.services.profile_service import ProfileService

router = APIRouter(prefix="/sections", tags=["sections"])

SECTION_SOURCE_TYPE = "portfolio_section"


def _service(session: DbSessionDep, llm_factory: LLMFactoryDep) -> GenerationService:
    return GenerationService(ProfileService(ProfileRepository(session)), llm_factory)


@router.get("", response_model=list[SectionResponse])
async def list_sections(
    current_user: CurrentUserDep, session: DbSessionDep, llm_factory: LLMFactoryDep
) -> list[SectionResponse]:
    sections = await _service(session, llm_factory).list_sections(current_user)
    return [SectionResponse.model_validate(section) for section in sections]


@router.patch("/{section_id}", response_model=SectionResponse)
async def update_section(
    section_id: uuid.UUID,
    payload: SectionUpdateRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    llm_factory: LLMFactoryDep,
    vector_store: VectorStoreDep,
    job_queue: JobQueueDep,
) -> SectionResponse:
    section = await _service(session, llm_factory).update_content(
        current_user, section_id, payload.content
    )
    # Manually edited content invalidates any embedding created for the previously-approved
    # version — it must be re-approved before it can ground recruiter chat answers again.
    await EmbeddingService(session, vector_store, job_queue).delete_embed(
        SECTION_SOURCE_TYPE, section.id
    )
    return SectionResponse.model_validate(section)


@router.post("/{section_id}/regenerate", response_model=SectionResponse)
@limiter.limit("10/hour")
async def regenerate_section(
    request: Request,
    section_id: uuid.UUID,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    llm_factory: LLMFactoryDep,
    vector_store: VectorStoreDep,
    job_queue: JobQueueDep,
) -> SectionResponse:
    section = await _service(session, llm_factory).regenerate(current_user, section_id)
    await EmbeddingService(session, vector_store, job_queue).delete_embed(
        SECTION_SOURCE_TYPE, section.id
    )
    return SectionResponse.model_validate(section)


@router.post("/{section_id}/approve", response_model=SectionResponse)
async def approve_section(
    section_id: uuid.UUID,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    llm_factory: LLMFactoryDep,
    vector_store: VectorStoreDep,
    job_queue: JobQueueDep,
) -> SectionResponse:
    generation_service = _service(session, llm_factory)
    section = await generation_service.approve(current_user, section_id)

    profile = await ProfileService(ProfileRepository(session)).get_or_create_for_user(current_user)
    await EmbeddingService(session, vector_store, job_queue).enqueue_embed(
        profile.id, SECTION_SOURCE_TYPE, section.id, section.content
    )
    return SectionResponse.model_validate(section)
