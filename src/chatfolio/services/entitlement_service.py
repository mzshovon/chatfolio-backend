import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.core.exceptions import ServiceUnavailableError
from chatfolio.entitlements.cache import ENTITLEMENT_TTL_SECONDS, EntitlementCache
from chatfolio.entitlements.types import Entitlements, FeatureValue
from chatfolio.models.billing import (
    EntitlementOverride,
    Plan,
    Subscription,
    SubscriptionStatus,
)

logger = structlog.get_logger(__name__)


class EntitlementService:
    """The single server-side authority on what a user may do (docs/PRICING_ENTITLEMENTS_PLAN.md).

    Resolution order: unexpired per-user override > active unexpired subscription's plan > the
    default free plan. Expiry is evaluated against `now()` on every read, never trusted to a cron.
    """

    def __init__(self, session: AsyncSession, cache: EntitlementCache) -> None:
        self._session = session
        self._cache = cache

    async def get(self, user_id: uuid.UUID) -> Entitlements:
        now = datetime.now(UTC)
        cached = await self._cache.get_entitlements(user_id)
        if cached is not None and (cached.valid_until is None or cached.valid_until > now):
            return cached

        entitlements = await self._resolve(user_id, now)
        ttl = ENTITLEMENT_TTL_SECONDS
        if entitlements.valid_until is not None:
            ttl = min(ttl, int((entitlements.valid_until - now).total_seconds()))
        if ttl > 0:
            await self._cache.set_entitlements(user_id, entitlements, ttl)
        return entitlements

    async def invalidate(self, user_id: uuid.UUID) -> None:
        await self._cache.invalidate(user_id)

    async def _resolve(self, user_id: uuid.UUID, now: datetime) -> Entitlements:
        subscription = (
            await self._session.execute(
                select(Subscription)
                .where(
                    Subscription.user_id == user_id,
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    or_(Subscription.expires_at.is_(None), Subscription.expires_at > now),
                )
                .limit(1)
            )
        ).scalar_one_or_none()

        if subscription is not None:
            plan = subscription.plan
            source = subscription.source.value
            expires_at = subscription.expires_at
        else:
            plan = await self._default_free_plan()
            source = "free"
            expires_at = None

        values = {
            f.feature_key: FeatureValue(f.int_value, f.bool_value, f.period) for f in plan.features
        }

        override_expiries: list[datetime] = []
        overrides = (
            await self._session.execute(
                select(EntitlementOverride).where(
                    EntitlementOverride.user_id == user_id,
                    or_(
                        EntitlementOverride.expires_at.is_(None),
                        EntitlementOverride.expires_at > now,
                    ),
                )
            )
        ).scalars()
        for override in overrides:
            base = values.get(override.feature_key, FeatureValue())
            values[override.feature_key] = FeatureValue(
                int_value=override.int_value if override.int_value is not None else base.int_value,
                bool_value=(
                    override.bool_value if override.bool_value is not None else base.bool_value
                ),
                period=base.period,
            )
            if override.expires_at is not None:
                override_expiries.append(override.expires_at)

        expiries = ([expires_at] if expires_at else []) + override_expiries
        return Entitlements(
            plan_slug=plan.slug,
            plan_name=plan.name,
            source=source,
            subscription_expires_at=expires_at,
            valid_until=min(expiries) if expiries else None,
            values=values,
        )

    async def _default_free_plan(self) -> Plan:
        plan = (
            await self._session.execute(select(Plan).where(Plan.is_default_free.is_(True)))
        ).scalar_one_or_none()
        if plan is None:
            # A missing free plan means the catalogue was never seeded. Failing closed and loudly
            # beats silently granting (or denying) everything.
            logger.error("entitlements.default_plan_missing")
            raise ServiceUnavailableError()
        return plan
