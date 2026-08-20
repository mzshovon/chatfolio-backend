import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import structlog
from sqlalchemy import select

from chatfolio.models.embedding import VectorEmbedding
from chatfolio.services.embedding_service import EMBEDDING_COLLECTION
from chatfolio.vectorstore.base import VectorStore

logger = structlog.get_logger(__name__)


async def embed_content_job(
    ctx: dict[str, Any], profile_id: str, source_type: str, source_id: str, chunk_text: str
) -> None:
    """Computes the embedding and upserts both the Chroma vector and its Postgres pointer row.

    Resources come from ctx (populated in workers/worker.py's on_startup), same pattern as
    parse_cv_job — keeps this a plain function tests can call with a hand-built ctx.
    """
    sessionmaker = ctx["sessionmaker"]
    vector_store: VectorStore = ctx["vector_store"]
    embed_texts: Callable[[list[str]], list[list[float]]] = ctx["embed_texts"]

    chroma_ref_id = f"{source_type}:{source_id}"

    try:
        embeddings = await asyncio.to_thread(embed_texts, [chunk_text])
        await vector_store.upsert(
            collection=EMBEDDING_COLLECTION,
            ids=[chroma_ref_id],
            embeddings=embeddings,
            documents=[chunk_text],
            metadatas=[
                {"profile_id": profile_id, "source_type": source_type, "source_id": source_id}
            ],
        )
    except Exception:
        logger.exception("embed_content_job.failed", chroma_ref_id=chroma_ref_id)
        return

    async with sessionmaker() as session:
        result = await session.execute(
            select(VectorEmbedding).where(VectorEmbedding.chroma_ref_id == chroma_ref_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = VectorEmbedding(
                profile_id=uuid.UUID(profile_id),
                source_type=source_type,
                source_id=source_id,
                chroma_ref_id=chroma_ref_id,
                chunk_text=chunk_text,
            )
            session.add(row)
        else:
            row.chunk_text = chunk_text
        await session.commit()
