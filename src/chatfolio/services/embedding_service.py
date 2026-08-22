import uuid
from collections.abc import Callable
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chatfolio.models.embedding import VectorEmbedding
from chatfolio.models.profile import Education, Experience, Project, Skill
from chatfolio.vectorstore.base import VectorStore
from chatfolio.workers.queue import JobQueue

logger = structlog.get_logger(__name__)

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


async def reconcile_missing_embeddings(
    sessionmaker: async_sessionmaker[AsyncSession], vector_store: VectorStore, job_queue: JobQueue
) -> int:
    """Self-healing check, run on worker startup: compares every `VectorEmbedding` pointer row
    in Postgres (source of truth for *what should be embedded*) against what Chroma actually has
    stored, and re-enqueues `embed_content_job` for any pointer whose vector has gone missing.

    Exists because this project has, more than once, had Chroma silently lose data it should
    have kept (a wrong volume mount path being the root cause found on 2026-08-22 — see
    BACKEND_PLAN.md's changelog) while the Postgres pointer rows stayed behind, looking valid.
    `embed_content_job` only writes its Postgres pointer *after* the Chroma upsert succeeds, so a
    pointer existing has always meant "this was embedded" — but it can't detect the vector being
    deleted out from under it later. This closes that gap without needing to know why the vector
    went missing. Re-embeds straight from `chunk_text` already stored on the pointer row, so it
    doesn't need to re-derive text from the original Experience/Project/Skill/Education rows.

    Cheap to run unconditionally on every startup: one Postgres query, one Chroma id listing, an
    in-memory set diff. Only enqueues work when something is actually missing.
    """
    async with sessionmaker() as session:
        result = await session.execute(select(VectorEmbedding))
        pointers = list(result.scalars().all())

    if not pointers:
        return 0

    existing_ids = set(await vector_store.list_ids(collection=EMBEDDING_COLLECTION))
    missing = [p for p in pointers if p.chroma_ref_id not in existing_ids]

    for pointer in missing:
        await job_queue.enqueue_job(
            "embed_content_job",
            str(pointer.profile_id),
            pointer.source_type,
            pointer.source_id,
            pointer.chunk_text,
        )

    if missing:
        logger.warning(
            "embeddings.reconciled_missing", missing_count=len(missing), total=len(pointers)
        )
    return len(missing)
