import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.core.exceptions import NotFoundError, ValidationFailedError
from chatfolio.entitlements.cache import EntitlementCache
from chatfolio.entitlements.catalog import FEATURES_BY_KEY, validate_feature_value
from chatfolio.entitlements.duration import compute_expiry
from chatfolio.models.billing import (
    EntitlementOverride,
    Plan,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
)
from chatfolio.models.user import User
from chatfolio.schemas.admin_billing import (
    OverrideSetRequest,
    SubscriptionAdminResponse,
    SubscriptionGrantRequest,
    UserSubscriptionResponse,
)
from chatfolio.services.audit import record_admin_action


class SubscriptionAdminService:
    """Manual grants/revocations and per-user overrides (support, promos, early launch before
    any payment strategy exists). The user's cached entitlements are dropped after each commit."""

    def __init__(self, session: AsyncSession, cache: EntitlementCache) -> None:
        self._session = session
        self._cache = cache

    async def get_subscriptions(self, user_id: uuid.UUID) -> UserSubscriptionResponse:
        await self._user(user_id)
        rows = list(
            (
                await self._session.execute(
                    select(Subscription)
                    .where(Subscription.user_id == user_id)
                    .order_by(Subscription.starts_at.desc())
                )
            ).scalars()
        )
        now = datetime.now(UTC)
        current = next(
            (
                r
                for r in rows
                if r.status == SubscriptionStatus.ACTIVE
                and (r.expires_at is None or r.expires_at > now)
            ),
            None,
        )
        return UserSubscriptionResponse(
            current=_to_response(current) if current else None,
            history=[_to_response(r) for r in rows],
        )

    async def grant(
        self, admin: User, user_id: uuid.UUID, payload: SubscriptionGrantRequest
    ) -> UserSubscriptionResponse:
        await self._user(user_id)
        plan = (
            await self._session.execute(select(Plan).where(Plan.slug == payload.plan_slug))
        ).scalar_one_or_none()
        if plan is None:
            raise NotFoundError(f"Plan {payload.plan_slug!r} not found.")
        if plan.is_default_free:
            raise ValidationFailedError(
                "The free plan needs no subscription — revoke the current one instead."
            )
        if not plan.is_active:
            raise ValidationFailedError("This plan is inactive and cannot be granted.")

        now = datetime.now(UTC)
        active = (
            await self._session.execute(
                select(Subscription).where(
                    Subscription.user_id == user_id,
                    Subscription.status == SubscriptionStatus.ACTIVE,
                )
            )
        ).scalar_one_or_none()

        start = now
        if payload.expires_at is not None:
            if payload.expires_at <= now:
                raise ValidationFailedError("expires_at must be in the future.")
            expires_at: datetime | None = payload.expires_at
        else:
            # Re-granting the same plan extends it from its current end rather than losing the
            # time the user already has.
            if (
                active is not None
                and active.plan_id == plan.id
                and active.expires_at is not None
                and active.expires_at > now
            ):
                start = active.expires_at
            expires_at = compute_expiry(start, plan.duration_unit, plan.duration_count)

        replaced: str | None = None
        if active is not None:
            replaced = str(active.id)
            active.status = SubscriptionStatus.CANCELLED
            # Flush before inserting: the one-active-subscription unique index is checked per
            # statement, so the cancel must land first.
            await self._session.flush()

        subscription = Subscription(
            user_id=user_id,
            plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            source=SubscriptionSource.ADMIN_GRANT,
            starts_at=now,
            expires_at=expires_at,
        )
        self._session.add(subscription)
        await self._session.flush()
        await record_admin_action(
            self._session,
            admin,
            "subscription.grant",
            "user",
            user_id,
            {
                "plan": plan.slug,
                "expires_at": expires_at.isoformat() if expires_at else None,
                "replaced_subscription": replaced,
                "reason": payload.reason,
            },
        )
        await self._commit_and_invalidate(user_id)
        return await self.get_subscriptions(user_id)

    async def revoke(self, admin: User, user_id: uuid.UUID) -> UserSubscriptionResponse:
        await self._user(user_id)
        active = (
            await self._session.execute(
                select(Subscription).where(
                    Subscription.user_id == user_id,
                    Subscription.status == SubscriptionStatus.ACTIVE,
                )
            )
        ).scalar_one_or_none()
        if active is None:
            raise NotFoundError("This user has no active subscription.")
        active.status = SubscriptionStatus.CANCELLED
        await self._session.flush()
        await record_admin_action(
            self._session,
            admin,
            "subscription.revoke",
            "user",
            user_id,
            {"subscription": str(active.id), "plan": active.plan.slug},
        )
        await self._commit_and_invalidate(user_id)
        return await self.get_subscriptions(user_id)

    # ---- overrides ----

    async def list_overrides(self, user_id: uuid.UUID) -> list[EntitlementOverride]:
        await self._user(user_id)
        return list(
            (
                await self._session.execute(
                    select(EntitlementOverride)
                    .where(EntitlementOverride.user_id == user_id)
                    .order_by(EntitlementOverride.feature_key)
                )
            ).scalars()
        )

    async def set_override(
        self, admin: User, user_id: uuid.UUID, feature_key: str, payload: OverrideSetRequest
    ) -> EntitlementOverride:
        await self._user(user_id)
        definition = FEATURES_BY_KEY.get(feature_key)
        if definition is None:
            raise NotFoundError(f"Unknown feature {feature_key!r}.")
        problem = validate_feature_value(definition, payload.int_value, payload.bool_value, None)
        if problem:
            raise ValidationFailedError(problem)
        if payload.expires_at is not None and payload.expires_at <= datetime.now(UTC):
            raise ValidationFailedError("expires_at must be in the future.")

        override = (
            await self._session.execute(
                select(EntitlementOverride).where(
                    EntitlementOverride.user_id == user_id,
                    EntitlementOverride.feature_key == feature_key,
                )
            )
        ).scalar_one_or_none()
        before = _override_dict(override)
        if override is None:
            override = EntitlementOverride(user_id=user_id, feature_key=feature_key)
            self._session.add(override)
        override.int_value = payload.int_value
        override.bool_value = payload.bool_value
        override.expires_at = payload.expires_at
        override.reason = payload.reason
        override.granted_by = admin.id
        await self._session.flush()
        await record_admin_action(
            self._session,
            admin,
            "override.set",
            "user",
            user_id,
            {"feature": feature_key, "from": before, "to": _override_dict(override)},
        )
        await self._commit_and_invalidate(user_id)
        return override

    async def delete_override(self, admin: User, user_id: uuid.UUID, feature_key: str) -> None:
        await self._user(user_id)
        override = (
            await self._session.execute(
                select(EntitlementOverride).where(
                    EntitlementOverride.user_id == user_id,
                    EntitlementOverride.feature_key == feature_key,
                )
            )
        ).scalar_one_or_none()
        if override is None:
            raise NotFoundError("No override exists for this feature.")
        before = _override_dict(override)
        await self._session.delete(override)
        await self._session.flush()
        await record_admin_action(
            self._session,
            admin,
            "override.delete",
            "user",
            user_id,
            {"feature": feature_key, "from": before},
        )
        await self._commit_and_invalidate(user_id)

    # ---- helpers ----

    async def _user(self, user_id: uuid.UUID) -> User:
        user = await self._session.get(User, user_id)
        if user is None:
            raise NotFoundError("User not found.")
        return user

    async def _commit_and_invalidate(self, user_id: uuid.UUID) -> None:
        await self._session.commit()
        await self._cache.invalidate(user_id)


def _to_response(subscription: Subscription) -> SubscriptionAdminResponse:
    return SubscriptionAdminResponse(
        id=subscription.id,
        plan_slug=subscription.plan.slug,
        plan_name=subscription.plan.name,
        status=subscription.status.value,
        source=subscription.source.value,
        starts_at=subscription.starts_at,
        expires_at=subscription.expires_at,
    )


def _override_dict(override: EntitlementOverride | None) -> dict[str, Any] | None:
    if override is None:
        return None
    return {
        "int_value": override.int_value,
        "bool_value": override.bool_value,
        "expires_at": override.expires_at.isoformat() if override.expires_at else None,
    }
