import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.models.embedding import VectorEmbedding
from chatfolio.models.profile import Education, Experience, Project, Skill
from chatfolio.vectorstore.base import VectorStore
from chatfolio.workers.queue import JobQueue

EMBEDDING_COLLECTION = "chatfolio_content"


def experience_chunk_text(experience: Experience) -> str:
    end = "present" if experience.is_current else (experience.end_date or "unknown end date")
    period = f"{experience.start_date or '?'} to {end}"
    description = experience.description or ""
    return f"{experience.role} at {experience.company} ({period}): {description}"


def project_chunk_text(project: Project) -> str:
    tech = ", ".join(project.tech_stack)
    return (
        f"Project: {project.title}. {project.description or ''} "
        f"Tech: {tech}. Impact: {project.impact or ''}"
    )


def skill_chunk_text(skill: Skill) -> str:
    parts = [f"Skill: {skill.name}"]
    if skill.category:
        parts.append(f"category: {skill.category}")
    if skill.proficiency:
        parts.append(f"proficiency: {skill.proficiency}")
    return ", ".join(parts)


def education_chunk_text(education: Education) -> str:
    return f"{education.degree or ''} in {education.field or ''} at {education.institution}"


# path (used as embedding source_type) -> (model, chunk-text builder). Single source of truth
# for "how do we turn a profile-owned row into embeddable text" — shared by the CRUD routes
# (api/v1/profiles.py) and scripts/backfill_embeddings.py, so they can never drift apart.
EMBEDDABLE_CHILD_TYPES: dict[str, tuple[type[Any], Callable[[Any], str]]] = {
    "experience": (Experience, experience_chunk_text),
    "projects": (Project, project_chunk_text),
    "skills": (Skill, skill_chunk_text),
    "education": (Education, education_chunk_text),
}


class EmbeddingService:
    """Owns the embedding lifecycle for any embeddable child row (Experience, Project, Skill,
    Education, approved PortfolioSection). Kept separate from ProfileService/GenerationService
    deliberately: those are generic across ALL child types (including CV and PortfolioSection,
    which must NOT auto-embed), so embedding side effects are wired in explicitly per call site
    rather than baked into the generic CRUD path.
    """

    def __init__(
        self, session: AsyncSession, vector_store: VectorStore, job_queue: JobQueue
    ) -> None:
        self._session = session
        self._vector_store = vector_store
        self._job_queue = job_queue

    async def enqueue_embed(
        self, profile_id: uuid.UUID, source_type: str, source_id: uuid.UUID, chunk_text: str
    ) -> None:
        if not chunk_text.strip():
            return
        await self._job_queue.enqueue_job(
            "embed_content_job", str(profile_id), source_type, str(source_id), chunk_text
        )

    async def delete_embed(self, source_type: str, source_id: uuid.UUID) -> None:
        chroma_ref_id = f"{source_type}:{source_id}"
        await self._vector_store.delete(collection=EMBEDDING_COLLECTION, ids=[chroma_ref_id])

        result = await self._session.execute(
            select(VectorEmbedding).where(VectorEmbedding.chroma_ref_id == chroma_ref_id)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()
