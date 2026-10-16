from redis.asyncio import Redis

from chatfolio.config.settings import get_settings

_client: Redis | None = None


def get_redis() -> Redis:
    """Lazy process-lifetime singleton (same shape as db/session.py and workers/queue.py): the
    connection pool is only built on first use, after tests' env-var overrides are in place."""
    global _client
    if _client is None:
        _client = Redis.from_url(get_settings().redis.url, decode_responses=True)
    return _client
