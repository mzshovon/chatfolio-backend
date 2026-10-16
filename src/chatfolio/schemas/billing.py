from datetime import datetime

from pydantic import BaseModel


class PriceResponse(BaseModel):
    amount_minor: int
    # Whole units as a string ("500", "499.50") so the UI never has to do currency maths.
    amount: str
    currency: str
    symbol: str


class DurationResponse(BaseModel):
    unit: str
    count: int
    # "month", "6 months", "year", ... — None for lifetime/free plans with nothing to show.
    label: str | None


class PlanCardResponse(BaseModel):
    slug: str
    name: str
    tagline: str
    is_free: bool
    price: PriceResponse
    duration: DurationResponse
    badge_text: str | None
    cta_label: str
    footnote: str
    trial_days: int
    bullets: list[str]


class PricingResponse(BaseModel):
    plans: list[PlanCardResponse]


class PlanSummaryResponse(BaseModel):
    slug: str
    name: str


class FeatureEntitlementResponse(BaseModel):
    kind: str
    value_type: str
    int_value: int | None
    bool_value: bool | None
    unlimited: bool
    period: str | None


class EntitlementsResponse(BaseModel):
    plan: PlanSummaryResponse
    # "free" or the subscription source ("support_kori_license", "trial", "admin_grant", ...)
    source: str
    expires_at: datetime | None
    features: dict[str, FeatureEntitlementResponse]
