import uuid
from datetime import datetime

from pydantic import BaseModel, Field


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
    # Where the CTA sends a buyer (the seller's product page); null until the admin sets one.
    purchase_url: str | None = None
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
    # Consumption in the current window (flow features) or right now (links, domains); null for
    # features that aren't countable (gates, history days). `remaining` is null when unlimited.
    used: int | None = None
    remaining: int | None = None


class EntitlementsResponse(BaseModel):
    plan: PlanSummaryResponse
    # "free" or the subscription source ("support_kori_license", "trial", "admin_grant", ...)
    source: str
    expires_at: datetime | None
    features: dict[str, FeatureEntitlementResponse]


class ModelChoiceResponse(BaseModel):
    id: str
    provider: str
    model_id: str
    display_name: str
    is_default: bool
    allowed: bool
    # None when selectable; "plan" = upgrade needed, "unavailable" = temporarily not offered.
    locked_reason: str | None


class MyModelsResponse(BaseModel):
    selection_enabled: bool
    selected_model_id: str | None
    # What will actually answer right now (the fallback when nothing valid is selected).
    effective_model_id: str
    models: list[ModelChoiceResponse]


class ModelPreferenceRequest(BaseModel):
    # null clears the choice (back to the default model).
    model_id: uuid.UUID | None


class ActivateLicenseRequest(BaseModel):
    license_key: str = Field(min_length=6, max_length=200)
    # Optional when only one plan can be activated with a license; required otherwise.
    plan_slug: str | None = Field(default=None, max_length=63)


class StartTrialRequest(BaseModel):
    plan_slug: str = Field(max_length=63)


class StrategyPlanResponse(BaseModel):
    slug: str
    name: str
    purchase_url: str | None


class StrategyResponse(BaseModel):
    name: str
    label: str
    plans: list[StrategyPlanResponse]


class TrialOfferResponse(BaseModel):
    plan_slug: str
    days: int


class StrategiesResponse(BaseModel):
    strategies: list[StrategyResponse]
    # Free trials this account can still start (empty once used or when not eligible).
    trials: list[TrialOfferResponse]
