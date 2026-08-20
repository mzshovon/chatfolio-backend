import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.models.embedding import VectorEmbedding
from chatfolio.vectorstore.base import VectorStore
from chatfolio.workers.queue import JobQueue

EMBEDDING_COLLECTION = "chatfolio_content"


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
