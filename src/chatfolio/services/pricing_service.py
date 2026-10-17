from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.entitlements.cache import EntitlementCache
from chatfolio.entitlements.catalog import UNLIMITED
from chatfolio.models.billing import DurationUnit, Plan
from chatfolio.schemas.billing import (
    DurationResponse,
    PlanCardResponse,
    PriceResponse,
    PricingResponse,
)

_CURRENCY_SYMBOLS = {"BDT": "৳", "USD": "$", "EUR": "€", "GBP": "£"}


class _BulletValues(dict[str, str]):
    """Unknown placeholders render as-is instead of raising — one typo in admin-written copy
    must not take the public pricing page down."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class PricingService:
    """Public pricing-card payload. Bullet text is a template rendered from the plan's *real*
    feature values, so the marketing copy cannot drift from what is actually enforced."""

    def __init__(self, session: AsyncSession, cache: EntitlementCache) -> None:
        self._session = session
        self._cache = cache

    async def get_pricing(self) -> PricingResponse:
        cached = await self._cache.get_pricing()
        if cached is not None:
            return PricingResponse.model_validate(cached)

        plans = (
            (
                await self._session.execute(
                    select(Plan)
                    .where(Plan.is_active.is_(True), Plan.is_public.is_(True))
                    .order_by(Plan.sort_order, Plan.created_at)
                )
            )
            .scalars()
            .all()
        )
        response = PricingResponse(plans=[_to_card(plan) for plan in plans])
        await self._cache.set_pricing(response.model_dump(mode="json"))
        return response


def _to_card(plan: Plan) -> PlanCardResponse:
    return PlanCardResponse(
        slug=plan.slug,
        name=plan.name,
        tagline=plan.tagline,
        is_free=plan.is_default_free,
        price=_price(plan),
        duration=DurationResponse(
            unit=plan.duration_unit.value,
            count=plan.duration_count,
            label=_duration_label(plan.duration_unit, plan.duration_count),
        ),
        badge_text=plan.badge_text,
        cta_label=plan.cta_label,
        footnote=plan.footnote,
        trial_days=plan.trial_days,
        purchase_url=plan.purchase_url,
        bullets=_render_bullets(plan),
    )


def _price(plan: Plan) -> PriceResponse:
    amount = Decimal(plan.price_minor) / 100
    text = f"{amount:.2f}".removesuffix(".00")
    return PriceResponse(
        amount_minor=plan.price_minor,
        amount=text,
        currency=plan.currency,
        symbol=_CURRENCY_SYMBOLS.get(plan.currency, plan.currency),
    )


def _duration_label(unit: DurationUnit, count: int) -> str | None:
    if unit == DurationUnit.LIFETIME or count <= 0:
        return None
    return unit.value if count == 1 else f"{count} {unit.value}s"


def _render_bullets(plan: Plan) -> list[str]:
    values = _BulletValues()
    for feature in plan.features:
        if feature.int_value is not None:
            values[feature.feature_key] = (
                "Unlimited" if feature.int_value == UNLIMITED else str(feature.int_value)
            )
        elif feature.bool_value is not None:
            values[feature.feature_key] = "Yes" if feature.bool_value else "No"
    return [bullet.format_map(values) for bullet in plan.marketing_bullets]
