import hashlib
import uuid
from collections.abc import Awaitable
from typing import Any, cast

import structlog
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.core.exceptions import LimitReachedError
from chatfolio.entitlements.catalog import FEATURES_BY_KEY, UNLIMITED, FeatureKey
from chatfolio.entitlements.periods import period_key, seconds_until_period_end
from chatfolio.entitlements.types import Entitlements
from chatfolio.models.billing import LLMUsage, QuotaPeriod, UsageCounter

logger = structlog.get_logger(__name__)

# Atomic check-and-increment. Returns {status, value}: status 1 = granted (value = new total),
# 0 = denied (value = current total), -1 = key missing (caller seeds it from Postgres, retries).
# limit < 0 means unlimited: still counted (for usage display), never denied.
_CONSUME_LUA = """
local cur = redis.call('GET', KEYS[1])
if not cur then return {-1, 0} end
cur = tonumber(cur)
local limit = tonumber(ARGV[1])
local amount = tonumber(ARGV[2])
if limit >= 0 and cur + amount > limit then return {0, cur} end
return {1, redis.call('INCRBY', KEYS[1], amount)}
"""

_RELEASE_LUA = """
local cur = redis.call('GET', KEYS[1])
if not cur then return 0 end
local new = tonumber(cur) - tonumber(ARGV[1])
if new < 0 then new = 0 end
redis.call('SET', KEYS[1], new, 'KEEPTTL')
return new
"""


class QuotaService:
    """Per-period consumption of flow features (chats, meetings, publishes, AI tokens).

    Two layers, by design (docs/PRICING_ENTITLEMENTS_PLAN.md §6):
      * Redis — the fast, atomic gate (one Lua script: check + increment).
      * Postgres `usage_counters` — the durable record, upserted in the *same transaction* as the
        action being counted, and the source a missing Redis key is rebuilt from. So losing or
        flushing Redis can never hand out free quota, and a Redis outage degrades to a row-locked
        Postgres check instead of failing open.
    A unit reserved in Redis but whose transaction later rolls back is conservative (counts
    against the user until the window rolls); callers `release()` on every failure they handle.
    """

    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self._session = session
        self._redis = redis

    # ---- flow quotas ----

    async def consume(
        self, user_id: uuid.UUID, feature: FeatureKey, entitlements: Entitlements, amount: int = 1
    ) -> int:
        """Reserve `amount`; raises LimitReachedError if it would exceed the plan's limit.
        Returns the new total for the window."""
        period = self._period(feature, entitlements)
        limit = entitlements.limit(feature)
        try:
            total = await self._redis_consume(user_id, feature, period, limit, amount)
        except LimitReachedError:
            raise
        except Exception:
            logger.warning("quota.redis_unavailable_using_db", feature=feature.value, exc_info=True)
            total = await self._db_consume(user_id, feature, period, limit, amount)
            return total
        await self._upsert(user_id, feature, period, amount)
        return total

    async def release(
        self, user_id: uuid.UUID, feature: FeatureKey, entitlements: Entitlements, amount: int = 1
    ) -> None:
        """Compensation for a consume() whose action then failed."""
        period = self._period(feature, entitlements)
        try:
            await self._eval(_RELEASE_LUA, self._key(user_id, feature, period), amount)
        except Exception:
            logger.warning("quota.release_failed", feature=feature.value, exc_info=True)
        await self._upsert(user_id, feature, period, -amount)

    async def add_usage(
        self, user_id: uuid.UUID, feature: FeatureKey, entitlements: Entitlements, amount: int
    ) -> None:
        """Unconditional tally (AI tokens are only known after the call): never denies."""
        if amount <= 0:
            return
        period = self._period(feature, entitlements)
        key = self._key(user_id, feature, period)
        try:
            if not await self._redis.exists(key):
                await self._seed(user_id, feature, period)
            await self._redis.incrby(key, amount)
        except Exception:
            logger.warning("quota.add_usage_redis_failed", feature=feature.value, exc_info=True)
        await self._upsert(user_id, feature, period, amount)

    async def used(
        self, user_id: uuid.UUID, feature: FeatureKey, entitlements: Entitlements
    ) -> int:
        period = self._period(feature, entitlements)
        key = self._key(user_id, feature, period)
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return await self._seed(user_id, feature, period)
            return int(raw)
        except Exception:
            return await self._db_used(user_id, feature, period)

    async def ensure_open(
        self, user_id: uuid.UUID, feature: FeatureKey, entitlements: Entitlements
    ) -> None:
        """Pre-check for tallied quotas (tokens): raises once the window's budget is spent."""
        limit = entitlements.limit(feature)
        if limit == UNLIMITED:
            return
        used = await self.used(user_id, feature, entitlements)
        if used >= limit:
            raise LimitReachedError(feature.value, limit, used)

    async def record_model_usage(self, user_id: uuid.UUID, model_label: str, tokens: int) -> None:
        """Per-model tokens/calls for the current month (cost reporting, not a quota)."""
        stmt = insert(LLMUsage).values(
            user_id=user_id,
            period_key=period_key(QuotaPeriod.MONTHLY),
            model_label=model_label,
            tokens=max(tokens, 0),
            calls=1,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "period_key", "model_label"],
            set_={"tokens": LLMUsage.tokens + max(tokens, 0), "calls": LLMUsage.calls + 1},
        )
        await self._session.execute(stmt)

    # ---- stock quotas ----

    async def lock(self, user_id: uuid.UUID, feature: FeatureKey) -> None:
        """Serialise concurrent stock changes (links, domains) for one user until commit, so two
        parallel requests can't both pass the count check."""
        digest = hashlib.sha256(f"{user_id}:{feature.value}".encode()).digest()
        key = int.from_bytes(digest[:8], "big") % (2**63)
        await self._session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    @staticmethod
    def ensure_room(feature: FeatureKey, entitlements: Entitlements, current: int) -> None:
        """Raises unless one more item fits under a stock limit (`current` = items already held)."""
        limit = entitlements.limit(feature)
        if limit != UNLIMITED and current >= limit:
            raise LimitReachedError(feature.value, limit, current)

    # ---- internals ----

    async def _eval(self, script: str, key: str, *args: int) -> Any:
        # redis-py types `eval` as Awaitable|sync because one signature serves both clients.
        return await cast(Awaitable[Any], self._redis.eval(script, 1, key, *(str(a) for a in args)))

    @staticmethod
    def _period(feature: FeatureKey, entitlements: Entitlements) -> QuotaPeriod:
        return (
            entitlements.period(feature)
            or FEATURES_BY_KEY[feature.value].default_period
            or QuotaPeriod.LIFETIME
        )

    @staticmethod
    def _key(user_id: uuid.UUID, feature: FeatureKey, period: QuotaPeriod) -> str:
        return f"q:{user_id}:{feature.value}:{period_key(period)}"

    async def _redis_consume(
        self, user_id: uuid.UUID, feature: FeatureKey, period: QuotaPeriod, limit: int, amount: int
    ) -> int:
        key = self._key(user_id, feature, period)
        for _ in range(2):
            status, value = await self._eval(_CONSUME_LUA, key, limit, amount)
            if status == 1:
                return int(value)
            if status == 0:
                raise LimitReachedError(feature.value, limit, int(value))
            await self._seed(user_id, feature, period)
        raise RuntimeError("quota key could not be seeded")

    async def _seed(self, user_id: uuid.UUID, feature: FeatureKey, period: QuotaPeriod) -> int:
        """Initialise a missing Redis counter from the durable row (NX: never clobbers a racer)."""
        used = await self._db_used(user_id, feature, period)
        ttl = seconds_until_period_end(period)
        await self._redis.set(self._key(user_id, feature, period), used, ex=ttl, nx=True)
        return used

    async def _db_used(self, user_id: uuid.UUID, feature: FeatureKey, period: QuotaPeriod) -> int:
        row = await self._session.execute(
            select(UsageCounter.used).where(
                UsageCounter.user_id == user_id,
                UsageCounter.feature_key == feature.value,
                UsageCounter.period_key == period_key(period),
            )
        )
        return row.scalar_one_or_none() or 0

    async def _upsert(
        self, user_id: uuid.UUID, feature: FeatureKey, period: QuotaPeriod, delta: int
    ) -> None:
        stmt = insert(UsageCounter).values(
            user_id=user_id,
            feature_key=feature.value,
            period_key=period_key(period),
            used=max(delta, 0),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "feature_key", "period_key"],
            set_={"used": _greatest0(UsageCounter.used + delta)},
        )
        await self._session.execute(stmt)

    async def _db_consume(
        self, user_id: uuid.UUID, feature: FeatureKey, period: QuotaPeriod, limit: int, amount: int
    ) -> int:
        """Redis-down path: same guarantee via a row lock instead of the Lua script."""
        await self._upsert(user_id, feature, period, 0)
        row = await self._session.execute(
            select(UsageCounter)
            .where(
                UsageCounter.user_id == user_id,
                UsageCounter.feature_key == feature.value,
                UsageCounter.period_key == period_key(period),
            )
            .with_for_update()
        )
        counter = row.scalar_one()
        if limit != UNLIMITED and counter.used + amount > limit:
            raise LimitReachedError(feature.value, limit, counter.used)
        counter.used += amount
        await self._session.flush()
        return counter.used


def _greatest0(expression: Any) -> Any:
    return func.greatest(expression, 0)
