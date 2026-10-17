import uuid
from datetime import UTC, datetime, timedelta

from chatfolio.core.exceptions import FeatureNotInPlanError
from chatfolio.entitlements.catalog import FeatureKey
from chatfolio.entitlements.types import Entitlements
from chatfolio.services.entitlement_service import EntitlementService
from chatfolio.services.quota_service import QuotaService


class EntitlementGuard:
    """What services call to enforce a plan: resolve entitlements, require a feature, consume or
    release quota, and apply the chat-history window. Every method re-reads the owner's current
    entitlements, so a plan change or an expiry applies on the very next call."""

    def __init__(self, entitlements: EntitlementService, quota: QuotaService) -> None:
        self._entitlements = entitlements
        self._quota = quota

    async def get(self, user_id: uuid.UUID) -> Entitlements:
        return await self._entitlements.get(user_id)

    async def require_feature(self, user_id: uuid.UUID, feature: FeatureKey) -> Entitlements:
        entitlements = await self._entitlements.get(user_id)
        if not entitlements.enabled(feature):
            raise FeatureNotInPlanError(feature.value)
        return entitlements

    async def consume(self, user_id: uuid.UUID, feature: FeatureKey, amount: int = 1) -> None:
        entitlements = await self._entitlements.get(user_id)
        await self._quota.consume(user_id, feature, entitlements, amount)

    async def release(self, user_id: uuid.UUID, feature: FeatureKey, amount: int = 1) -> None:
        entitlements = await self._entitlements.get(user_id)
        await self._quota.release(user_id, feature, entitlements, amount)

    async def add_usage(self, user_id: uuid.UUID, feature: FeatureKey, amount: int) -> None:
        entitlements = await self._entitlements.get(user_id)
        await self._quota.add_usage(user_id, feature, entitlements, amount)

    async def ensure_open(self, user_id: uuid.UUID, feature: FeatureKey) -> None:
        entitlements = await self._entitlements.get(user_id)
        await self._quota.ensure_open(user_id, feature, entitlements)

    async def used(self, user_id: uuid.UUID, feature: FeatureKey) -> int:
        entitlements = await self._entitlements.get(user_id)
        return await self._quota.used(user_id, feature, entitlements)

    async def lock_and_check_room(
        self, user_id: uuid.UUID, feature: FeatureKey, current_count: int
    ) -> None:
        """Stock limits (links, domains): call `lock_stock` first, count the existing items, then
        this — the lock is what makes the count-then-insert race-free."""
        entitlements = await self._entitlements.get(user_id)
        self._quota.ensure_room(feature, entitlements, current_count)

    async def lock_stock(self, user_id: uuid.UUID, feature: FeatureKey) -> None:
        await self._quota.lock(user_id, feature)

    async def history_cutoff(self, user_id: uuid.UUID) -> datetime | None:
        """Oldest conversation start the owner may see; None = everything."""
        entitlements = await self._entitlements.get(user_id)
        if entitlements.is_unlimited(FeatureKey.CHAT_HISTORY_DAYS):
            return None
        return datetime.now(UTC) - timedelta(days=entitlements.limit(FeatureKey.CHAT_HISTORY_DAYS))
