from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.billing.strategy import StrategyName
from chatfolio.billing.support_kori import SupportKoriLicenseStrategy
from chatfolio.config.settings import Settings
from chatfolio.core.redis import get_redis
from chatfolio.entitlements.cache import EntitlementCache
from chatfolio.models.billing import License, LicenseStatus, Plan, Subscription, SubscriptionStatus
from chatfolio.models.profile import CandidateProfile
from chatfolio.models.user import User
from chatfolio.services.billing_service import BillingService
from chatfolio.services.sms_service import SmsService, SmsTemplate, resolve_candidate_mobile

logger = structlog.get_logger(__name__)

REMINDER_LEAD = timedelta(days=3)
REVALIDATION_BATCH = 100


def _billing_service(
    session: AsyncSession, settings: Settings, ctx: dict[str, Any]
) -> BillingService:
    redis = get_redis()
    # Worker startup supplies the strategy; tests substitute a fake seller.
    strategy = ctx.get("license_strategy") or SupportKoriLicenseStrategy(settings.support_kori)
    return BillingService(
        session,
        EntitlementCache(redis),
        redis,
        settings.security,
        settings.support_kori,
        {StrategyName.SUPPORT_KORI_LICENSE: strategy},
    )


async def expire_subscriptions_job(ctx: dict[str, Any]) -> int:
    """Housekeeping only: flips `status` on subscriptions past their end so reports and the
    one-active-per-user index stay tidy. Access never depends on this — entitlements are judged
    from `expires_at` on every read, so a late or missed run cannot extend anyone's plan."""
    async with ctx["sessionmaker"]() as session:
        result = await session.execute(
            update(Subscription)
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.expires_at.is_not(None),
                Subscription.expires_at < datetime.now(UTC),
            )
            .values(status=SubscriptionStatus.EXPIRED)
        )
        await session.commit()
        count = int(result.rowcount or 0)
    if count:
        logger.info("billing.subscriptions_expired", count=count)
    return count


async def revalidate_licenses_job(ctx: dict[str, Any]) -> dict[str, int]:
    """Re-checks live licenses with the seller to catch refunds. Only an explicit "not valid"
    ends a plan; an unreachable seller (or any error) leaves everyone's access untouched."""
    settings: Settings = ctx["settings"]
    stale_before = datetime.now(UTC) - timedelta(days=settings.support_kori.revalidate_after_days)
    outcome = {"still_valid": 0, "revoked": 0, "unknown": 0}

    async with ctx["sessionmaker"]() as session:
        licenses = (
            (
                await session.execute(
                    select(License)
                    .join(Subscription, Subscription.order_id == License.order_id)
                    .where(
                        License.status == LicenseStatus.ACTIVE,
                        Subscription.status == SubscriptionStatus.ACTIVE,
                        or_(
                            License.last_revalidated_at.is_(None),
                            License.last_revalidated_at < stale_before,
                        ),
                        or_(
                            License.last_revalidated_at.is_not(None),
                            License.validated_at < stale_before,
                        ),
                    )
                    .order_by(License.last_revalidated_at.asc().nulls_first())
                    .limit(REVALIDATION_BATCH)
                )
            )
            .scalars()
            .all()
        )
        service = _billing_service(session, settings, ctx)
        for license_ in licenses:
            try:
                result = await service.revalidate_license(license_)
            except Exception:
                logger.exception("billing.revalidate_failed", license=str(license_.id))
                await session.rollback()
                result = None
            key = "unknown" if result is None else ("still_valid" if result else "revoked")
            outcome[key] += 1
    if any(outcome.values()):
        logger.info("billing.licenses_revalidated", **outcome)
    return outcome


async def expiry_reminder_job(ctx: dict[str, Any]) -> int:
    """One email + SMS per subscription, once it is within 3 days of ending."""
    settings: Settings = ctx["settings"]
    now = datetime.now(UTC)
    sent = 0

    async with ctx["sessionmaker"]() as session:
        rows = (
            await session.execute(
                select(Subscription, User, Plan)
                .join(User, User.id == Subscription.user_id)
                .join(Plan, Plan.id == Subscription.plan_id)
                .where(
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.expires_at.is_not(None),
                    Subscription.expires_at > now,
                    Subscription.expires_at <= now + REMINDER_LEAD,
                    Subscription.reminder_sent_at.is_(None),
                )
            )
        ).all()
        for subscription, user, plan in rows:
            assert subscription.expires_at is not None
            end_date = subscription.expires_at.astimezone(UTC).strftime("%d %b %Y")
            profile = (
                await session.execute(
                    select(CandidateProfile).where(CandidateProfile.user_id == user.id)
                )
            ).scalar_one_or_none()
            name = (profile.full_name if profile else None) or "there"
            await _notify(ctx, settings, user, profile, name, plan.name, end_date)
            subscription.reminder_sent_at = now
            sent += 1
        await session.commit()
    if sent:
        logger.info("billing.expiry_reminders_sent", count=sent)
    return sent


async def _notify(
    ctx: dict[str, Any],
    settings: Settings,
    user: User,
    profile: CandidateProfile | None,
    name: str,
    plan_name: str,
    end_date: str,
) -> None:
    """Best effort on both channels: a mail/SMS gateway outage must not stop the other channel or
    leave the reminder to be re-sent on every run."""
    try:
        await ctx["email_sender"].send(
            to=user.email,
            subject=f"Your Chatfolio {plan_name} plan ends on {end_date}",
            body=(
                f"Hi {name},\n\nYour Chatfolio {plan_name} plan ends on {end_date}. After that "
                "your account returns to the free plan.\n\nTo keep every feature, activate a new "
                f"license key: {settings.app.frontend_base_url}/pricing\n"
            ),
        )
    except Exception:
        logger.warning("billing.reminder_email_failed", user_id=str(user.id), exc_info=True)
    mobile = resolve_candidate_mobile(profile.phone if profile else None, user.phone)
    if mobile is None:
        return
    try:
        await ctx["sms_sender"].send(
            to=mobile,
            message=SmsService.render(
                SmsTemplate.SUBSCRIPTION_EXPIRING, name=name, plan=plan_name, end_date=end_date
            ),
        )
    except Exception:
        logger.warning("billing.reminder_sms_failed", user_id=str(user.id), exc_info=True)
