import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, EmailStr

from chatfolio.models.profile import ProfileStatus


class ProfileUpdateRequest(BaseModel):
    full_name: str | None = None
    title: str | None = None
    bio: str | None = None
    location: str | None = None
    contact_email: EmailStr | None = None
    phone: str | None = None
    social_links: dict[str, str] | None = None


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str | None
    title: str | None
    bio: str | None
    location: str | None
    contact_email: str | None
    phone: str | None
    social_links: dict[str, str]
    status: ProfileStatus


class ExperienceRequest(BaseModel):
    company: str
    role: str
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    description: str | None = None


class ExperienceUpdateRequest(BaseModel):
    company: str | None = None
    role: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool | None = None
    description: str | None = None


class ExperienceResponse(ExperienceRequest):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID


class ProjectRequest(BaseModel):
    title: str
    description: str | None = None
    tech_stack: list[str] = []
    impact: str | None = None
    links: dict[str, str] = {}


class ProjectUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    tech_stack: list[str] | None = None
    impact: str | None = None
    links: dict[str, str] | None = None


class ProjectResponse(ProjectRequest):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID


class SkillRequest(BaseModel):
    name: str
    category: str | None = None
    proficiency: str | None = None


class SkillUpdateRequest(BaseModel):
    name: str | None = None
    category: str | None = None
    proficiency: str | None = None


class SkillResponse(SkillRequest):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID


class EducationRequest(BaseModel):
    institution: str
    degree: str | None = None
    field: str | None = None
    start_date: date | None = None
    end_date: date | None = None


class EducationUpdateRequest(BaseModel):
    institution: str | None = None
    degree: str | None = None
    field: str | None = None
    start_date: date | None = None
    end_date: date | None = None


class EducationResponse(EducationRequest):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
