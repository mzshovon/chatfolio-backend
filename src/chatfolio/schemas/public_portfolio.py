from pydantic import BaseModel

from chatfolio.models.profile import JobType
from chatfolio.schemas.profile import (
    EducationResponse,
    ExperienceResponse,
    ProjectResponse,
    SkillResponse,
    WorkHours,
)


class PublicChatfolioResponse(BaseModel):
    slug: str
    full_name: str | None
    title: str | None
    location: str | None
    job_type: JobType | None
    contact_email: str | None
    phone: str | None
    social_links: dict[str, str]
    intro: str | None
    summary: str | None
    experiences: list[ExperienceResponse]
    projects: list[ProjectResponse]
    skills: list[SkillResponse]
    education: list[EducationResponse]
    contact_cta_config: dict[str, str]
    cv_downloadable: bool
    recruiter_count: int
    # False when the owner's plan removes the Chatfolio badge.
    show_badge: bool = True


class PortfolioSearchResult(BaseModel):
    slug: str
    full_name: str | None
    recruiter_count: int
    title: str | None
    location: str | None
    avatar_url: str | None
    headline: str | None  # <=140 chars, from the approved intro or summary
    current_company: str | None
    job_type: JobType | None
    years_experience: float | None
    notice_period_days: int | None  # 0 means immediately
    top_skills: list[str]  # up to 5
    work_hours: WorkHours | None
