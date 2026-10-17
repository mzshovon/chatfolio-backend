import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select

from chatfolio.core.redis import get_redis
from chatfolio.db.session import get_sessionmaker
from chatfolio.entitlements.cache import EntitlementCache
from chatfolio.models.billing import (
    EntitlementOverride,
    Plan,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
)


async def user_id_of(client: AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me", headers=headers)).json()["id"])


async def grant_plan(user_id: uuid.UUID, slug: str = "premium", *, days: int | None = 180) -> None:
    """Gives a user a paid plan directly in the DB (and drops their cached entitlements)."""
    async with get_sessionmaker()() as session:
        plan = (await session.execute(select(Plan).where(Plan.slug == slug))).scalar_one()
        session.add(
            Subscription(
                user_id=user_id,
                plan_id=plan.id,
                status=SubscriptionStatus.ACTIVE,
                source=SubscriptionSource.ADMIN_GRANT,
                expires_at=datetime.now(UTC) + timedelta(days=days) if days is not None else None,
            )
        )
        await session.commit()
    await EntitlementCache(get_redis()).invalidate(user_id)


async def grant_premium(client: AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    user_id = await user_id_of(client, headers)
    await grant_plan(user_id)
    return user_id


async def set_override(
    user_id: uuid.UUID,
    feature_key: str,
    *,
    int_value: int | None = None,
    bool_value: bool | None = None,
) -> None:
    """Pins one feature for one user (as an admin override would) and drops their cache."""
    async with get_sessionmaker()() as session:
        existing = (
            await session.execute(
                select(EntitlementOverride).where(
                    EntitlementOverride.user_id == user_id,
                    EntitlementOverride.feature_key == feature_key,
                )
            )
        ).scalar_one_or_none()
        row = existing or EntitlementOverride(user_id=user_id, feature_key=feature_key)
        row.int_value = int_value
        row.bool_value = bool_value
        row.reason = "test"
        session.add(row)
        await session.commit()
    await EntitlementCache(get_redis()).invalidate(user_id)
