import asyncio
from typing import Protocol

from arq import create_pool
from arq.connections import ArqRedis
from arq.connections import RedisSettings as ArqRedisSettings

from chatfolio.config.settings import get_settings


class JobQueue(Protocol):
    async def enqueue_job(self, function: str, *args: object) -> object | None: ...


_pool: ArqRedis | None = None
_pool_lock = asyncio.Lock()


async def get_arq_pool() -> ArqRedis:
    """Lazy singleton, mirroring the get_engine()/get_sessionmaker() pattern in db/session.py.

    Deliberately not built at FastAPI startup: httpx's ASGITransport (used in tests) never
    triggers lifespan events, so a startup-hook-based pool would simply never exist in tests.
    """
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                redis = get_settings().redis
                _pool = await create_pool(
                    ArqRedisSettings(host=redis.host, port=redis.port, database=redis.db)
                )
    return _pool
