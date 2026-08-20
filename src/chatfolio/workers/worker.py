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


async def shutdown(ctx: dict[str, Any]) -> None:
    pass


class WorkerSettings:
    functions: list[Any] = [parse_cv_job, embed_content_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = ArqRedisSettings(
        host=settings.redis.host, port=settings.redis.port, database=settings.redis.db
    )
