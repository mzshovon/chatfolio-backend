from pydantic import BaseModel

from chatfolio.models.profile import JobType
from chatfolio.schemas.profile import (
    EducationResponse,
    ExperienceResponse,
    ProjectResponse,
    SkillResponse,
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


class PortfolioSearchResult(BaseModel):
    slug: str
    full_name: str | None
    recruiter_count: int
