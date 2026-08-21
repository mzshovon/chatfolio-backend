from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from chatfolio.api.deps import DbSessionDep, StorageBackendDep
from chatfolio.core.exceptions import NotFoundError, ServiceUnavailableError
from chatfolio.models.portfolio_section import SectionType
from chatfolio.models.profile import Education, Experience, Project, Skill
from chatfolio.repositories.profile_repository import ProfileRepository
from chatfolio.schemas.profile import (
    EducationResponse,
    ExperienceResponse,
    ProjectResponse,
    SkillResponse,
)
from chatfolio.schemas.public_portfolio import PublicChatfolioResponse
from chatfolio.services.portfolio_service import PublicPortfolioService

router = APIRouter(prefix="/public/chatfolio", tags=["public"])


def _service(session: DbSessionDep) -> PublicPortfolioService:
    return PublicPortfolioService(session, ProfileRepository(session))


@router.get("/{slug}", response_model=None)
async def get_public_chatfolio(
    slug: str, session: DbSessionDep
) -> PublicChatfolioResponse | RedirectResponse:
    service = _service(session)
    chatfolio = await service.get_published_by_slug(slug)

    if chatfolio is None:
        redirect_slug = await service.find_redirect_target(slug)
        if redirect_slug is not None:
            return RedirectResponse(url=f"/v1/public/chatfolio/{redirect_slug}", status_code=307)
        raise NotFoundError("This Chatfolio is not available.")

    profile = await service.get_profile(chatfolio.profile_id)
    if profile is None:
        # Should be unreachable (FK CASCADE ties profile lifetime to the chatfolio row), but an
        # `assert` here would silently vanish under `python -O` and crash on `.full_name` below
        # with a confusing AttributeError instead of a clean error — raise explicitly.
        raise ServiceUnavailableError("This Chatfolio is temporarily unavailable.")

    sections = await service.list_approved_sections(chatfolio.profile_id)
    experiences = await service.list_children(Experience, chatfolio.profile_id)
    projects = await service.list_children(Project, chatfolio.profile_id)
    skills = await service.list_children(Skill, chatfolio.profile_id)
    education = await service.list_children(Education, chatfolio.profile_id)

    return PublicChatfolioResponse(
        slug=chatfolio.slug,
        full_name=profile.full_name,
        title=profile.title,
        location=profile.location,
        contact_email=profile.contact_email,
        phone=profile.phone,
        social_links=profile.social_links,
        intro=sections.get(SectionType.INTRO),
        summary=sections.get(SectionType.SUMMARY),
        experiences=[ExperienceResponse.model_validate(e) for e in experiences],
        projects=[ProjectResponse.model_validate(p) for p in projects],
        skills=[SkillResponse.model_validate(s) for s in skills],
        education=[EducationResponse.model_validate(e) for e in education],
        contact_cta_config=chatfolio.contact_cta_config,
        cv_downloadable=chatfolio.cv_downloadable,
    )


@router.get("/{slug}/cv", response_model=None)
async def download_public_cv(
    slug: str, session: DbSessionDep, storage: StorageBackendDep
) -> RedirectResponse:
    service = _service(session)
    chatfolio = await service.get_published_by_slug(slug)
    if chatfolio is None or not chatfolio.cv_downloadable:
        raise NotFoundError("A downloadable CV is not available for this Chatfolio.")

    cv = await service.get_latest_parsed_cv(chatfolio.profile_id)
    if cv is None:
        raise NotFoundError("A downloadable CV is not available for this Chatfolio.")

    url = await storage.generate_download_url(cv.file_key)
    return RedirectResponse(url=url)
