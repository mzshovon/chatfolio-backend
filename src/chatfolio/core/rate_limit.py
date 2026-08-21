from slowapi import Limiter
from slowapi.util import get_remote_address

from chatfolio.config.settings import get_settings


def _redis_uri() -> str:
    redis = get_settings().redis
    return f"redis://{redis.host}:{redis.port}/{redis.db}"


# get_settings() is only evaluated lazily on first use of `limiter` (Limiter's __init__ reads
# storage_uri immediately, but this module itself isn't imported until main.py wires it up,
# which happens after conftest.py's env-var overrides in tests — same ordering guarantee
# db/session.py and workers/queue.py rely on).
limiter = Limiter(key_func=get_remote_address, storage_uri=_redis_uri())
