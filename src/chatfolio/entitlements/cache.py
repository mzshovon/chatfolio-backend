import json
import uuid
from typing import Any

import structlog
from redis.asyncio import Redis

from chatfolio.entitlements.types import Entitlements

logger = structlog.get_logger(__name__)

_CATALOG_VERSION_KEY = "ent:catalog_ver"
ENTITLEMENT_TTL_SECONDS = 300
CATALOG_TTL_SECONDS = 600


class EntitlementCache:
    """Redis cache for resolved entitlements and the public pricing payload.

    Plan/feature/model edits affect many users at once, so they call `bump_catalog_version()`
    instead of scanning for keys: every cached value embeds the version in its key, so old
    entries simply stop being read and age out via TTL. A single user's change (grant, purchase)
    uses `invalidate(user_id)`.

    **Fail-soft by design:** every Redis error is logged and treated as a cache miss/no-op, so a
    Redis outage slows requests down (recompute from Postgres) but can never grant or deny access
    incorrectly — the DB remains the source of truth.
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def catalog_version(self) -> int:
        try:
            raw = await self._redis.get(_CATALOG_VERSION_KEY)
            return int(raw) if raw else 0
        except Exception:
            logger.warning("entitlements.cache_version_failed", exc_info=True)
            return 0

    async def bump_catalog_version(self) -> None:
        try:
            await self._redis.incr(_CATALOG_VERSION_KEY)
        except Exception:
            logger.warning("entitlements.cache_bump_failed", exc_info=True)

    async def get_entitlements(self, user_id: uuid.UUID) -> Entitlements | None:
        data = await self._get_json(await self._user_key(user_id))
        if data is None:
            return None
        try:
            return Entitlements.from_json(data)
        except (KeyError, ValueError, TypeError):
            logger.warning("entitlements.cache_corrupt", user_id=str(user_id))
            return None

    async def set_entitlements(
        self, user_id: uuid.UUID, entitlements: Entitlements, ttl_seconds: int
    ) -> None:
        await self._set_json(await self._user_key(user_id), entitlements.to_json(), ttl_seconds)

    async def invalidate(self, user_id: uuid.UUID) -> None:
        try:
            await self._redis.delete(await self._user_key(user_id))
        except Exception:
            logger.warning("entitlements.cache_invalidate_failed", exc_info=True)

    async def get_catalog(self, name: str) -> Any | None:
        """A shared, version-keyed catalogue payload (pricing cards, model catalogue, ...)."""
        return await self._get_json(f"plans:{name}:v{await self.catalog_version()}")

    async def set_catalog(self, name: str, payload: Any) -> None:
        key = f"plans:{name}:v{await self.catalog_version()}"
        await self._set_json(key, payload, CATALOG_TTL_SECONDS)

    async def get_pricing(self) -> Any | None:
        return await self.get_catalog("public")

    async def set_pricing(self, payload: Any) -> None:
        await self.set_catalog("public", payload)

    async def flush_all(self) -> None:
        """Test helper: drop every key this cache owns (never touches rate-limit/queue keys)."""
        async for key in self._redis.scan_iter(match="ent:*"):
            await self._redis.delete(key)
        async for key in self._redis.scan_iter(match="plans:*"):
            await self._redis.delete(key)

    async def _user_key(self, user_id: uuid.UUID) -> str:
        return f"ent:v{await self.catalog_version()}:{user_id}"

    async def _get_json(self, key: str) -> Any | None:
        try:
            raw = await self._redis.get(key)
            return json.loads(raw) if raw else None
        except Exception:
            logger.warning("entitlements.cache_read_failed", key=key, exc_info=True)
            return None

    async def _set_json(self, key: str, payload: Any, ttl_seconds: int) -> None:
        try:
            await self._redis.set(key, json.dumps(payload), ex=ttl_seconds)
        except Exception:
            logger.warning("entitlements.cache_write_failed", key=key, exc_info=True)
