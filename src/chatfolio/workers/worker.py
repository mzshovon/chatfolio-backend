import asyncio
from typing import Any

from arq.connections import RedisSettings as ArqRedisSettings

from chatfolio.config.settings import get_settings
from chatfolio.db.session import get_sessionmaker
from chatfolio.llm.factory import LLMProviderFactory
from chatfolio.storage.s3_storage import S3StorageBackend
from chatfolio.vectorstore.chroma_store import ChromaVectorStore
from chatfolio.vectorstore.local_embedder import embed_texts
from chatfolio.workers.jobs_cv import parse_cv_job
from chatfolio.workers.jobs_embedding import embed_content_job

settings = get_settings()


async def startup(ctx: dict[str, Any]) -> None:
    ctx["settings"] = settings
    ctx["sessionmaker"] = get_sessionmaker()
    ctx["storage"] = S3StorageBackend(settings.storage)
    ctx["llm_factory"] = LLMProviderFactory(settings.llm)
    ctx["vector_store"] = ChromaVectorStore(settings.vectorstore)
    ctx["embed_texts"] = embed_texts

    # Pre-warm the local embedding model serially, before any job can run: embed_texts()
    # downloads an ~80MB ONNX model lazily on its *first* call with no locking around that
    # check-and-fetch. arq runs jobs concurrently, so a burst of embed_content_job calls that
    # all hit "first call" at once (e.g. right after scripts/backfill_embeddings.py) each spawn
    # their own thread independently racing to download the same file — observed directly as
    # a dozen simultaneous progress bars fighting over the same bandwidth. One warm-up call
    # here means every real job call afterward just hits the on-disk cache.
    await asyncio.to_thread(embed_texts, ["warmup"])


async def shutdown(ctx: dict[str, Any]) -> None:
    pass


class WorkerSettings:
    functions: list[Any] = [parse_cv_job, embed_content_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = ArqRedisSettings(
        host=settings.redis.host, port=settings.redis.port, database=settings.redis.db
    )
