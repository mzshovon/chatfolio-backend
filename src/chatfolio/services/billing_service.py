import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.billing.strategy import (
    STRATEGY_LABELS,
    ActivationStrategy,
    StrategyName,
)
from chatfolio.config.settings import SecuritySettings, SupportKoriSettings
from chatfolio.core.exceptions import (
    ChatfolioError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
    TooManyRequestsError,
    ValidationFailedError,
)
from chatfolio.core.security import decrypt_secret, encrypt_secret
from chatfolio.entitlements.cache import EntitlementCache
from chatfolio.entitlements.duration import compute_expiry
from chatfolio.models.billing import (
    License,
    LicenseStatus,
    Order,
    OrderStatus,
    Plan,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
)
from chatfolio.models.user import TwoFactorMethod, User
from chatfolio.schemas.admin_billing import UserSubscriptionResponse
from chatfolio.services.audit import record_admin_action
from chatfolio.services.billing_settings_service import BillingSettings, BillingSettingsService
from chatfolio.services.subscription_admin_service import SubscriptionAdminService

logger = structlog.get_logger(__name__)

_MIN_KEY, _MAX_KEY = 6, 200
INVALID_LICENSE = "This license key is not valid."
EXPIRED_LICENSE = "This license has already been used and has expired. Please purchase a new one."


class PhoneVerificationRequiredError(ForbiddenError):
    code = "PHONE_VERIFICATION_REQUIRED"
    message = "Verify your phone number (two-factor with phone) to start a free trial."


class BillingService:
    """Turns a genuine purchase into a subscription.

    The strategy (Support Kori today, our own gateway later) only answers "is this purchase real?";
    `grant_from_purchase` is the one shared place that writes the order + subscription and clears
    the user's cached entitlements, so adding a strategy never touches entitlements or admin.
    """

    def __init__(
        self,
        session: AsyncSession,
        cache: EntitlementCache,
        redis: Redis,
        security: SecuritySettings,
        kori: SupportKoriSettings,
        strategies: dict[StrategyName, ActivationStrategy],
    ) -> None:
        self._session = session
        self._cache = cache
        self._redis = redis
        self._security = security
        self._kori = kori
        self._strategies = strategies
        self._settings_service = BillingSettingsService(session, cache)

    # ---- reads ----

    async def get_state(self, user_id: uuid.UUID) -> UserSubscriptionResponse:
        return await SubscriptionAdminService(self._session, self._cache).get_subscriptions(user_id)

    async def list_strategies(self, user: User) -> dict[str, Any]:
        """What the buy flow should offer: each enabled strategy with the plans it can sell, plus
        any free trial this user can still start."""
        settings = await self._settings_service.get()
        plans = await self._sellable_plans()
        offered = []
        for name in settings.enabled_strategies:
            if StrategyName(name) not in self._strategies:
                continue
            offered.append(
                {
                    "name": name,
                    "label": STRATEGY_LABELS[StrategyName(name)],
                    "plans": [
                        {"slug": p.slug, "name": p.name, "purchase_url": p.purchase_url}
                        for p in plans
                        if name in p.allowed_strategies
                    ],
                }
            )
        trials = []
        if await self._trial_eligibility_problem(user) is None:
            trials = [
                {"plan_slug": p.slug, "days": p.trial_days} for p in plans if p.trial_days > 0
            ]
        return {"strategies": offered, "trials": trials}

    # ---- license activation (Support Kori) ----

    async def activate_license(
        self, user: User, license_key: str, plan_slug: str | None
    ) -> UserSubscriptionResponse:
        settings = await self._settings_service.get()
        strategy = self._strategy(StrategyName.SUPPORT_KORI_LICENSE, settings)
        key = license_key.strip()
        if not _MIN_KEY <= len(key) <= _MAX_KEY:
            raise ValidationFailedError(INVALID_LICENSE)
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        # Before anything else, so replaying keys that belong to others is throttled too.
        await self._ensure_attempt_budget(user.id)

        # 1. Our own records first. Support Kori keeps answering "valid" for a key forever, so
        #    "already used" and "expired" can only come from here.
        existing = await self._license_by_hash(key_hash)
        if existing is not None:
            return await self._existing_license_outcome(existing, user)

        plan = await self._resolve_plan(plan_slug, StrategyName.SUPPORT_KORI_LICENSE)
        product_ref = plan.external_product_ref or self._kori.product_id
        if not product_ref:
            logger.error("billing.support_kori_product_id_missing", plan=plan.slug)
            raise ServiceUnavailableError("Activation is not available right now.")

        # 2. Ask the seller (no DB lock held across the network call).
        verification = await strategy.verify(credential=key, product_ref=product_ref)
        if not verification.valid:
            await self._record_failed_attempt(user.id)
            raise ValidationFailedError(INVALID_LICENSE)
        if settings.max_activation_delay_days is not None and verification.purchased_at:
            age = datetime.now(UTC) - verification.purchased_at.astimezone(UTC)
            if age > timedelta(days=settings.max_activation_delay_days):
                await self._record_failed_attempt(user.id)
                raise ValidationFailedError(
                    "This license was bought too long ago to be activated. Please contact support."
                )

        # 3. Claim the key and grant the plan atomically. UNIQUE(license_key_hash) is what makes
        #    "one key, one user" hold even if two requests race.
        await self._lock_user(user.id)
        now = datetime.now(UTC)
        try:
            async with self._session.begin_nested():
                order, _ = await self.grant_from_purchase(
                    user.id,
                    plan,
                    strategy=StrategyName.SUPPORT_KORI_LICENSE,
                    external_ref=key_hash,
                    purchased_at=verification.purchased_at,
                    raw=verification.raw,
                    now=now,
                )
                self._session.add(
                    License(
                        license_key_hash=key_hash,
                        license_key_encrypted=encrypt_secret(key, self._security),
                        key_last4=key[-4:],
                        user_id=user.id,
                        plan_id=plan.id,
                        order_id=order.id,
                        validated_at=now,
                    )
                )
                await self._session.flush()
        except IntegrityError:
            raced = await self._license_by_hash(key_hash)
            if raced is not None:
                return await self._existing_license_outcome(raced, user)
            raise ConflictError("Another activation is in progress. Please try again.") from None

        await self._session.commit()
        await self._cache.invalidate(user.id)
        logger.info("billing.license_activated", user_id=str(user.id), plan=plan.slug)
        return await self.get_state(user.id)

    async def _existing_license_outcome(
        self, license_: License, user: User
    ) -> UserSubscriptionResponse:
        if license_.user_id != user.id:
            # Someone else's key: indistinguishable from a wrong one, and counts as a bad guess.
            await self._record_failed_attempt(user.id)
            raise ValidationFailedError(INVALID_LICENSE)
        if license_.status != LicenseStatus.ACTIVE:
            raise ValidationFailedError(INVALID_LICENSE)
        subscription = (
            await self._session.execute(
                select(Subscription).where(Subscription.order_id == license_.order_id)
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        if (
            subscription is not None
            and subscription.status == SubscriptionStatus.ACTIVE
            and (subscription.expires_at is None or subscription.expires_at > now)
        ):
            return await self.get_state(user.id)  # re-submitting your own live key: idempotent
        raise ValidationFailedError(EXPIRED_LICENSE)

    # ---- the shared core every strategy ends in ----

    async def grant_from_purchase(
        self,
        user_id: uuid.UUID,
        plan: Plan,
        *,
        strategy: StrategyName,
        external_ref: str,
        purchased_at: datetime | None,
        raw: dict[str, Any] | None,
        now: datetime,
    ) -> tuple[Order, Subscription]:
        """Records the order and starts (or extends) the subscription. Does not commit."""
        active = (
            await self._session.execute(
                select(Subscription).where(
                    Subscription.user_id == user_id,
                    Subscription.status == SubscriptionStatus.ACTIVE,
                )
            )
        ).scalar_one_or_none()

        start = now
        # Buying the plan you already have stacks the new term after the current one.
        if (
            active is not None
            and active.plan_id == plan.id
            and active.expires_at is not None
            and active.expires_at > now
        ):
            start = active.expires_at
        expires_at = compute_expiry(start, plan.duration_unit, plan.duration_count)

        if active is not None:
            active.status = SubscriptionStatus.CANCELLED
            await self._session.flush()  # the one-active index is checked per statement

        order = Order(
            user_id=user_id,
            plan_id=plan.id,
            strategy=strategy.value,
            external_ref=external_ref,
            status=OrderStatus.PAID,
            amount_minor=plan.price_minor,
            currency=plan.currency,
            purchased_at=purchased_at,
            raw_response=raw,
        )
        self._session.add(order)
        await self._session.flush()
        subscription = Subscription(
            user_id=user_id,
            plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            source=SubscriptionSource(strategy.value),
            starts_at=now,
            expires_at=expires_at,
            order_id=order.id,
        )
        self._session.add(subscription)
        await self._session.flush()
        return order, subscription

    # ---- trial ----

    async def start_trial(self, user: User, plan_slug: str) -> UserSubscriptionResponse:
        plan = (
            await self._session.execute(select(Plan).where(Plan.slug == plan_slug))
        ).scalar_one_or_none()
        if plan is None or not plan.is_active or plan.is_default_free or plan.trial_days <= 0:
            raise NotFoundError("No free trial is available for this plan.")
        await self._lock_user(user.id)
        problem = await self._trial_eligibility_problem(user)
        if problem is not None:
            raise problem

        now = datetime.now(UTC)
        self._session.add(
            Subscription(
                user_id=user.id,
                plan_id=plan.id,
                status=SubscriptionStatus.ACTIVE,
                source=SubscriptionSource.TRIAL,
                starts_at=now,
                expires_at=now + timedelta(days=plan.trial_days),
            )
        )
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            raise ConflictError("You have already used your free trial.") from None
        await self._session.commit()
        await self._cache.invalidate(user.id)
        return await self.get_state(user.id)

    async def _trial_eligibility_problem(self, user: User) -> ChatfolioError | None:
        # "Verified phone" = a phone that went through the existing 2FA OTP enrolment.
        if not (
            user.phone
            and user.two_factor_enabled
            and user.two_factor_method in (TwoFactorMethod.PHONE, TwoFactorMethod.BOTH)
        ):
            return PhoneVerificationRequiredError()
        had_trial = (
            await self._session.execute(
                select(Subscription.id).where(
                    Subscription.user_id == user.id,
                    Subscription.source == SubscriptionSource.TRIAL,
                )
            )
        ).first()
        if had_trial is not None:
            return ConflictError("You have already used your free trial.")
        # One trial per phone number, however many accounts it is attached to.
        phone_reused = (
            await self._session.execute(
                select(Subscription.id)
                .join(User, User.id == Subscription.user_id)
                .where(Subscription.source == SubscriptionSource.TRIAL, User.phone == user.phone)
            )
        ).first()
        if phone_reused is not None:
            return ConflictError("A free trial was already used with this phone number.")
        now = datetime.now(UTC)
        active = (
            await self._session.execute(
                select(Subscription.id).where(
                    Subscription.user_id == user.id,
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    (Subscription.expires_at.is_(None)) | (Subscription.expires_at > now),
                )
            )
        ).first()
        if active is not None:
            return ConflictError("You already have an active plan.")
        return None

    # ---- revocation & re-validation ----

    async def revoke_license(self, license_: License, *, reason: str) -> None:
        """Ends the license, its order and the subscription it created; the user drops back to
        free. Commits and clears the user's cached entitlements."""
        license_.status = LicenseStatus.REVOKED
        order = await self._session.get(Order, license_.order_id)
        if order is not None:
            order.status = OrderStatus.REVOKED
        subscription = (
            await self._session.execute(
                select(Subscription).where(
                    Subscription.order_id == license_.order_id,
                    Subscription.status == SubscriptionStatus.ACTIVE,
                )
            )
        ).scalar_one_or_none()
        if subscription is not None:
            subscription.status = SubscriptionStatus.CANCELLED
        await self._session.commit()
        await self._cache.invalidate(license_.user_id)
        logger.warning(
            "billing.license_revoked",
            user_id=str(license_.user_id),
            license=str(license_.id),
            reason=reason,
        )

    async def revalidate_license(self, license_: License) -> bool | None:
        """Re-checks a license with the seller. True = still valid, False = revoked here,
        None = couldn't tell (seller outage): never downgrade anyone on an error."""
        plan = await self._session.get(Plan, license_.plan_id)
        product_ref = (plan.external_product_ref if plan else None) or self._kori.product_id
        if not product_ref:
            return None
        strategy = self._strategies.get(StrategyName.SUPPORT_KORI_LICENSE)
        if strategy is None:
            return None
        key = decrypt_secret(license_.license_key_encrypted, self._security)
        try:
            verification = await strategy.verify(credential=key, product_ref=product_ref)
        except ServiceUnavailableError:
            return None
        if verification.valid:
            license_.last_revalidated_at = datetime.now(UTC)
            await self._session.commit()
            return True
        await self.revoke_license(license_, reason="seller reports the license invalid")
        return False

    # ---- admin ----

    async def admin_revoke_license(self, admin: User, license_id: uuid.UUID) -> None:
        license_ = await self._session.get(License, license_id)
        if license_ is None:
            raise NotFoundError("License not found.")
        if license_.status == LicenseStatus.REVOKED:
            raise ConflictError("This license is already revoked.")
        await record_admin_action(
            self._session,
            admin,
            "license.revoke",
            "license",
            license_.id,
            {"user_id": str(license_.user_id), "key_last4": license_.key_last4},
        )
        await self.revoke_license(license_, reason=f"revoked by admin {admin.id}")

    # ---- helpers ----

    def _strategy(self, name: StrategyName, settings: BillingSettings) -> ActivationStrategy:
        strategy = self._strategies.get(name)
        if strategy is None or name.value not in settings.enabled_strategies:
            raise NotFoundError("This way of activating a plan is not available.")
        return strategy

    async def _sellable_plans(self) -> list[Plan]:
        return list(
            (
                await self._session.execute(
                    select(Plan)
                    .where(
                        Plan.is_active.is_(True),
                        Plan.is_public.is_(True),
                        Plan.is_default_free.is_(False),
                    )
                    .order_by(Plan.sort_order, Plan.created_at)
                )
            ).scalars()
        )

    async def _resolve_plan(self, plan_slug: str | None, strategy: StrategyName) -> Plan:
        plans = [p for p in await self._sellable_plans() if strategy.value in p.allowed_strategies]
        if plan_slug is None:
            if len(plans) != 1:
                raise ValidationFailedError("Choose which plan this license is for.")
            return plans[0]
        plan = next((p for p in plans if p.slug == plan_slug), None)
        if plan is None:
            raise NotFoundError("That plan cannot be activated this way.")
        return plan

    async def _license_by_hash(self, key_hash: str) -> License | None:
        return (
            await self._session.execute(select(License).where(License.license_key_hash == key_hash))
        ).scalar_one_or_none()

    async def _lock_user(self, user_id: uuid.UUID) -> None:
        digest = hashlib.sha256(f"billing:{user_id}".encode()).digest()
        key = int.from_bytes(digest[:8], "big") % (2**63)
        await self._session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    # Wrong-key throttle: counts only keys the seller rejected (not outages, not successes).
    def _attempts_key(self, user_id: uuid.UUID) -> str:
        return f"billing:failed:{user_id}"

    async def _ensure_attempt_budget(self, user_id: uuid.UUID) -> None:
        try:
            raw = await self._redis.get(self._attempts_key(user_id))
        except Exception:
            logger.warning("billing.attempt_check_failed", exc_info=True)
            return
        if raw is not None and int(raw) >= self._kori.max_failed_attempts_per_hour:
            raise TooManyRequestsError("Too many invalid license attempts. Please try again later.")

    async def _record_failed_attempt(self, user_id: uuid.UUID) -> None:
        try:
            key = self._attempts_key(user_id)
            count = await self._redis.incr(key)
            if count == 1:
                await self._redis.expire(key, 3600)
        except Exception:
            logger.warning("billing.attempt_record_failed", exc_info=True)
