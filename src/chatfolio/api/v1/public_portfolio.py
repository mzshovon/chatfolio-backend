from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse

from chatfolio.api.deps import PublicPortfolioServiceDep, StorageBackendDep
from chatfolio.core.exceptions import (
    NotFoundError,
    ServiceUnavailableError,
    ValidationFailedError,
)
from chatfolio.models.chatfolio import PublicChatfolio
from chatfolio.models.portfolio_section import SectionType
from chatfolio.models.profile import (
    CandidateProfile,
    Education,
    Experience,
    JobType,
    Project,
    Skill,
)
from chatfolio.schemas.profile import (
    EducationResponse,
    ExperienceResponse,
    ProjectResponse,
    SkillResponse,
)
from chatfolio.schemas.public_portfolio import PortfolioSearchResult, PublicChatfolioResponse
from chatfolio.services.portfolio_service import PublicPortfolioService

router = APIRouter(prefix="/public/chatfolio", tags=["public"])


async def _build_response(
    service: PublicPortfolioService, chatfolio: PublicChatfolio, profile: CandidateProfile
) -> PublicChatfolioResponse:
    sections = await service.list_approved_sections(chatfolio.profile_id)
    experiences = await service.list_children(Experience, chatfolio.profile_id)
    projects = await service.list_children(Project, chatfolio.profile_id)
    skills = await service.list_children(Skill, chatfolio.profile_id)
    education = await service.list_children(Education, chatfolio.profile_id)
    recruiter_count = await service.count_identified_recruiters(chatfolio.id)

    return PublicChatfolioResponse(
        slug=chatfolio.slug,
        full_name=profile.full_name,
        title=profile.title,
        location=profile.location,
        job_type=profile.job_type,
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
        recruiter_count=recruiter_count,
        show_badge=await service.show_badge(profile.user_id),
    )


@router.get("/search", response_model=list[PortfolioSearchResult], tags=["public"])
async def search_public_chatfolios(
    service: PublicPortfolioServiceDep,
    q: str | None = Query(
        default=None,
        description="Free text, e.g. 'React developer remote' or 'Python Dhaka'. Terms are ANDed "
        "across name, title, bio, location, roles and skills; remote/hybrid/onsite set the "
        "work mode.",
    ),
    username: str | None = Query(default=None, description="Partial match on the public slug."),
    location: str | None = Query(default=None, description="Partial match on candidate location."),
    job_type: JobType | None = Query(
        default=None, alias="work", description="Work filter: remote, hybrid or onsite."
    ),
    field: str | None = Query(
        default=None, description="Partial match on candidate title, e.g. 'Software Engineer'."
    ),
    start: Literal["immediately", "30"] | None = Query(
        default=None, description="Start filter: 'immediately' or '30' (<= 30 days notice)."
    ),
    exp: Literal["0-2", "3-5", "6+"] | None = Query(
        default=None, description="Experience filter in years."
    ),
    overlap: bool = Query(
        default=False, description="Only candidates whose work hours overlap the hours below."
    ),
    my_start: str | None = Query(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$"),
    my_end: str | None = Query(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$"),
    my_timezone: str | None = Query(default=None, description="IANA timezone, e.g. Asia/Dhaka."),
) -> list[PortfolioSearchResult]:
    overlap_hours = None
    if overlap:
        if not (my_start and my_end and my_timezone):
            raise ValidationFailedError(
                "overlap requires my_start, my_end and my_timezone."
            )
        try:
            ZoneInfo(my_timezone)
        except Exception as exc:
            raise ValidationFailedError("my_timezone is not a valid IANA timezone.") from exc
        overlap_hours = {"start": my_start, "end": my_end, "timezone": my_timezone}

    hits = await service.search_published(
        q=q,
        username=username,
        location=location,
        job_type=job_type,
        field=field,
        start=start,
        exp=exp,
        overlap_hours=overlap_hours,
    )
    return [
        PortfolioSearchResult(
            slug=hit.chatfolio.slug,
            full_name=hit.profile.full_name,
            recruiter_count=await service.count_identified_recruiters(hit.chatfolio.id),
            title=hit.profile.title,
            location=hit.profile.location,
            avatar_url=hit.profile.avatar_url,
            headline=hit.headline,
            current_company=hit.current_company,
            job_type=hit.profile.job_type,
            years_experience=hit.years_experience,
            notice_period_days=hit.profile.notice_period_days,
            top_skills=hit.top_skills,
            work_hours=hit.profile.work_hours,
        )
        for hit in hits
    ]


@router.get("/{slug}", response_model=None)
async def get_public_chatfolio(
    slug: str, service: PublicPortfolioServiceDep
) -> PublicChatfolioResponse | RedirectResponse:
    chatfolio = await service.get_published_by_slug(slug)

    if chatfolio is None:
        redirect_slug = await service.find_redirect_target(slug)
        if redirect_slug is not None:
            return RedirectResponse(
                url=f"/api/v1/public/chatfolio/{redirect_slug}", status_code=307
            )
        raise NotFoundError("This Chatfolio is not available.")

    profile = await service.get_profile(chatfolio.profile_id)
    if profile is None:
        # Should be unreachable (FK CASCADE ties profile lifetime to the chatfolio row), but an
        # `assert` here would silently vanish under `python -O` and crash on `.full_name` below
        # with a confusing AttributeError instead of a clean error — raise explicitly.
        raise ServiceUnavailableError("This Chatfolio is temporarily unavailable.")

    response = await _build_response(service, chatfolio, profile)
    await service.record_visit(chatfolio.id)
    return response


@router.get("/{slug}/cv", response_model=None)
async def download_public_cv(
    slug: str, service: PublicPortfolioServiceDep, storage: StorageBackendDep
) -> RedirectResponse:
    chatfolio = await service.get_published_by_slug(slug)
    if chatfolio is None or not chatfolio.cv_downloadable:
        raise NotFoundError("A downloadable CV is not available for this Chatfolio.")

    cv = await service.get_latest_parsed_cv(chatfolio.profile_id)
    if cv is None:
        raise NotFoundError("A downloadable CV is not available for this Chatfolio.")

    url = await storage.generate_download_url(cv.file_key)
    return RedirectResponse(url=url)
