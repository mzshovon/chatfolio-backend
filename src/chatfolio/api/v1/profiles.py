import uuid
from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, status
from pydantic import BaseModel

from chatfolio.api.deps import CurrentUserDep, DbSessionDep, JobQueueDep, VectorStoreDep
from chatfolio.models.mixins import ProfileChildMixin
from chatfolio.models.profile import Education, Experience, Project, Skill
from chatfolio.repositories.profile_repository import ProfileRepository
from chatfolio.schemas.profile import (
    EducationRequest,
    EducationResponse,
    EducationUpdateRequest,
    ExperienceRequest,
    ExperienceResponse,
    ExperienceUpdateRequest,
    ProfileResponse,
    ProfileUpdateRequest,
    ProjectRequest,
    ProjectResponse,
    ProjectUpdateRequest,
    SkillRequest,
    SkillResponse,
    SkillUpdateRequest,
)
from chatfolio.services.embedding_service import EmbeddingService
from chatfolio.services.profile_service import ProfileService

router = APIRouter(prefix="/profiles/me")

ChildT = TypeVar("ChildT", bound=ProfileChildMixin)
RequestT = TypeVar("RequestT", bound=BaseModel)
UpdateT = TypeVar("UpdateT", bound=BaseModel)
ResponseT = TypeVar("ResponseT", bound=BaseModel)


def _service(session: DbSessionDep) -> ProfileService:
    return ProfileService(ProfileRepository(session))


@router.get("", response_model=ProfileResponse, tags=["profile"])
async def get_my_profile(current_user: CurrentUserDep, session: DbSessionDep) -> ProfileResponse:
    profile = await _service(session).get_or_create_for_user(current_user)
    return ProfileResponse.model_validate(profile)


@router.patch("", response_model=ProfileResponse, tags=["profile"])
async def update_my_profile(
    payload: ProfileUpdateRequest, current_user: CurrentUserDep, session: DbSessionDep
) -> ProfileResponse:
    profile = await _service(session).update(current_user, payload)
    return ProfileResponse.model_validate(profile)


def register_child_routes(  # noqa: UP047 (kept consistent with TypeVar style used elsewhere)
    *,
    path: str,
    tag: str,
    model: type[ChildT],
    request_schema: type[RequestT],
    update_schema: type[UpdateT],
    response_schema: type[ResponseT],
    chunk_text: Callable[[ChildT], str],
) -> None:
    """Registers list/create/update/delete routes for one CandidateProfile child resource.

    Experience/Project/Skill/Education are structurally identical CRUD resources scoped to
    the current user's profile, so this factory avoids four near-duplicate route modules.
    `path` doubles as the embedding `source_type` (e.g. "experience", "skills") since these
    are exactly the resource types this factory registers.

    Unlike PortfolioSection (AI-authored, must be explicitly approved before it can ground
    recruiter chat — see api/v1/sections.py), these rows are the candidate's own directly-
    entered facts: there's no hallucination risk to review, so create/update re-embeds
    immediately rather than waiting on a separate approval step.
    """

    list_response_model: type = list[response_schema]  # type: ignore[valid-type]

    @router.get(f"/{path}", response_model=list_response_model, name=f"list_{path}", tags=[tag])
    async def list_items(current_user: CurrentUserDep, session: DbSessionDep) -> list[ChildT]:
        service = _service(session)
        return await service.list_children(current_user, model)

    @router.post(
        f"/{path}",
        response_model=response_schema,
        status_code=status.HTTP_201_CREATED,
        name=f"create_{path}",
        tags=[tag],
    )
    async def create_item(
        payload: request_schema,  # type: ignore[valid-type]
        current_user: CurrentUserDep,
        session: DbSessionDep,
        vector_store: VectorStoreDep,
        job_queue: JobQueueDep,
    ) -> ChildT:
        service = _service(session)
        item = model(**payload.model_dump())  # type: ignore[attr-defined]
        item = await service.add_child(current_user, item)

        profile = await service.get_or_create_for_user(current_user)
        await EmbeddingService(session, vector_store, job_queue).enqueue_embed(
            profile.id, path, item.id, chunk_text(item)
        )
        return item

    update_path = f"/{path}/{{item_id}}"

    @router.patch(update_path, response_model=response_schema, name=f"update_{path}", tags=[tag])
    async def update_item(
        item_id: uuid.UUID,
        payload: update_schema,  # type: ignore[valid-type]
        current_user: CurrentUserDep,
        session: DbSessionDep,
        vector_store: VectorStoreDep,
        job_queue: JobQueueDep,
    ) -> ChildT:
        service = _service(session)
        updates = payload.model_dump(exclude_unset=True)  # type: ignore[attr-defined]
        item = await service.update_child(current_user, model, item_id, updates)

        profile = await service.get_or_create_for_user(current_user)
        await EmbeddingService(session, vector_store, job_queue).enqueue_embed(
            profile.id, path, item.id, chunk_text(item)
        )
        return item

    @router.delete(
        update_path, status_code=status.HTTP_204_NO_CONTENT, name=f"delete_{path}", tags=[tag]
    )
    async def delete_item(
        item_id: uuid.UUID,
        current_user: CurrentUserDep,
        session: DbSessionDep,
        vector_store: VectorStoreDep,
        job_queue: JobQueueDep,
    ) -> None:
        service = _service(session)
        await service.delete_child(current_user, model, item_id)
        await EmbeddingService(session, vector_store, job_queue).delete_embed(path, item_id)


def _experience_chunk(experience: Experience) -> str:
    end = "present" if experience.is_current else (experience.end_date or "unknown end date")
    period = f"{experience.start_date or '?'} to {end}"
    description = experience.description or ""
    return f"{experience.role} at {experience.company} ({period}): {description}"


def _project_chunk(project: Project) -> str:
    tech = ", ".join(project.tech_stack)
    return (
        f"Project: {project.title}. {project.description or ''} "
        f"Tech: {tech}. Impact: {project.impact or ''}"
    )


def _skill_chunk(skill: Skill) -> str:
    parts = [f"Skill: {skill.name}"]
    if skill.category:
        parts.append(f"category: {skill.category}")
    if skill.proficiency:
        parts.append(f"proficiency: {skill.proficiency}")
    return ", ".join(parts)


def _education_chunk(education: Education) -> str:
    return f"{education.degree or ''} in {education.field or ''} at {education.institution}"


register_child_routes(
    path="experience",
    tag="experience",
    model=Experience,
    request_schema=ExperienceRequest,
    update_schema=ExperienceUpdateRequest,
    response_schema=ExperienceResponse,
    chunk_text=_experience_chunk,
)
register_child_routes(
    path="projects",
    tag="projects",
    model=Project,
    request_schema=ProjectRequest,
    update_schema=ProjectUpdateRequest,
    response_schema=ProjectResponse,
    chunk_text=_project_chunk,
)
register_child_routes(
    path="skills",
    tag="skills",
    model=Skill,
    request_schema=SkillRequest,
    update_schema=SkillUpdateRequest,
    response_schema=SkillResponse,
    chunk_text=_skill_chunk,
)
register_child_routes(
    path="education",
    tag="education",
    model=Education,
    request_schema=EducationRequest,
    update_schema=EducationUpdateRequest,
    response_schema=EducationResponse,
    chunk_text=_education_chunk,
)
