import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from chatfolio.core.exceptions import LimitReachedError
from chatfolio.core.redis import get_redis
from chatfolio.db.session import get_sessionmaker
from chatfolio.entitlements.cache import EntitlementCache
from chatfolio.entitlements.catalog import FeatureKey
from chatfolio.entitlements.periods import period_key, seconds_until_period_end
from chatfolio.entitlements.types import Entitlements, FeatureValue
from chatfolio.models.billing import QuotaPeriod, UsageCounter
from chatfolio.services.quota_service import QuotaService
from tests.factories.plans import user_id_of
from tests.factories.publish_flow import authed_client

CHATS = FeatureKey.RECRUITER_CHATS_LIMIT


def _ent(limit: int, period: QuotaPeriod = QuotaPeriod.MONTHLY) -> Entitlements:
    return Entitlements(
        "t", "T", "free", None, None, {CHATS.value: FeatureValue(int_value=limit, period=period)}
    )


async def _user() -> uuid.UUID:
    client, headers = await authed_client(f"quota-{uuid.uuid4().hex[:8]}@example.com")
    return await user_id_of(client, headers)


async def _consume(user_id: uuid.UUID, ent: Entitlements, amount: int = 1) -> int:
    """One consume in its own session + commit, as a real request would do."""
    async with get_sessionmaker()() as session:
        total = await QuotaService(session, get_redis()).consume(user_id, CHATS, ent, amount)
        await session.commit()
        return total


async def _db_used(user_id: uuid.UUID) -> int:
    async with get_sessionmaker()() as session:
        return (
            await session.execute(
                select(UsageCounter.used).where(
                    UsageCounter.user_id == user_id, UsageCounter.feature_key == CHATS.value
                )
            )
        ).scalar_one_or_none() or 0


async def test_consume_counts_up_to_the_limit_then_denies_with_details() -> None:
    user_id = await _user()
    ent = _ent(2)

    assert await _consume(user_id, ent) == 1
    assert await _consume(user_id, ent) == 2
    with pytest.raises(LimitReachedError) as denied:
        await _consume(user_id, ent)

    assert denied.value.extra == {
        "feature": "recruiter_chats_limit",
        "limit": 2,
        "used": 2,
        "upgrade_url": "/pricing",
    }
    assert await _db_used(user_id) == 2  # the denied attempt left no trace


async def test_unlimited_is_counted_but_never_denied() -> None:
    user_id = await _user()
    for _ in range(5):
        await _consume(user_id, _ent(-1))
    assert await _db_used(user_id) == 5


async def test_release_gives_the_unit_back() -> None:
    user_id = await _user()
    ent = _ent(1)
    await _consume(user_id, ent)
    async with get_sessionmaker()() as session:
        await QuotaService(session, get_redis()).release(user_id, CHATS, ent)
        await session.commit()

    assert await _consume(user_id, ent) == 1  # fits again
    assert await _db_used(user_id) == 1


async def test_concurrent_consumers_never_exceed_the_limit() -> None:
    user_id = await _user()
    ent = _ent(3)

    results = await asyncio.gather(
        *(_consume(user_id, ent) for _ in range(12)), return_exceptions=True
    )

    granted = [r for r in results if isinstance(r, int)]
    denied = [r for r in results if isinstance(r, LimitReachedError)]
    assert len(granted) == 3 and len(denied) == 9
    assert sorted(granted) == [1, 2, 3]
    assert await _db_used(user_id) == 3


async def test_flushing_redis_cannot_hand_out_free_quota() -> None:
    user_id = await _user()
    ent = _ent(2)
    await _consume(user_id, ent)
    await _consume(user_id, ent)

    # Simulate Redis losing every counter (restart, eviction, manual flush).
    async for key in get_redis().scan_iter(match=f"q:{user_id}:*"):
        await get_redis().delete(key)

    with pytest.raises(LimitReachedError):  # rebuilt from usage_counters, not restarted at 0
        await _consume(user_id, ent)


async def test_redis_outage_degrades_to_a_row_locked_database_check() -> None:
    class DownRedis:
        async def eval(self, *_: object) -> None:
            raise ConnectionError("redis down")

        async def get(self, *_: object) -> None:
            raise ConnectionError("redis down")

    user_id = await _user()
    ent = _ent(1)

    async def consume_with_redis_down() -> int:
        async with get_sessionmaker()() as session:
            total = await QuotaService(session, DownRedis()).consume(  # type: ignore[arg-type]
                user_id, CHATS, ent
            )
            await session.commit()
            return total

    assert await consume_with_redis_down() == 1
    with pytest.raises(LimitReachedError):  # fails closed, never open
        await consume_with_redis_down()


async def test_add_usage_tallies_without_ever_denying_and_ensure_open_then_blocks() -> None:
    user_id = await _user()
    ent = Entitlements(
        "t",
        "T",
        "free",
        None,
        None,
        {FeatureKey.AI_TOKENS_LIMIT.value: FeatureValue(100, None, QuotaPeriod.MONTHLY)},
    )
    async with get_sessionmaker()() as session:
        quota = QuotaService(session, get_redis())
        await quota.ensure_open(user_id, FeatureKey.AI_TOKENS_LIMIT, ent)  # fresh: fine
        await quota.add_usage(user_id, FeatureKey.AI_TOKENS_LIMIT, ent, 250)  # overshoot allowed
        assert await quota.used(user_id, FeatureKey.AI_TOKENS_LIMIT, ent) == 250
        with pytest.raises(LimitReachedError):
            await quota.ensure_open(user_id, FeatureKey.AI_TOKENS_LIMIT, ent)
        await session.commit()


def test_period_keys_and_ttls() -> None:
    now = datetime(2026, 12, 31, 23, 0, tzinfo=UTC)
    assert period_key(QuotaPeriod.MONTHLY, now) == "2026-12"
    assert period_key(QuotaPeriod.DAILY, now) == "2026-12-31"
    assert period_key(QuotaPeriod.LIFETIME, now) == "lifetime"
    assert seconds_until_period_end(QuotaPeriod.LIFETIME, now) is None
    # 1h to midnight + 3 days grace; year rollover handled.
    assert seconds_until_period_end(QuotaPeriod.MONTHLY, now) == 3600 + 3 * 86400


async def test_quota_cache_is_independent_of_entitlement_cache() -> None:
    # Flushing the entitlement cache (as tests and plan edits do) must not reset consumption.
    user_id = await _user()
    ent = _ent(1)
    await _consume(user_id, ent)
    await EntitlementCache(get_redis()).flush_all()
    with pytest.raises(LimitReachedError):
        await _consume(user_id, ent)
