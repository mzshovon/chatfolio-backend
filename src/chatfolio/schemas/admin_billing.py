import uuid
from datetime import datetime
from decimal import Decimal
from string import Formatter
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from chatfolio.billing.strategy import StrategyName
from chatfolio.config.settings import LLMProviderName
from chatfolio.entitlements.catalog import FEATURES_BY_KEY
from chatfolio.models.billing import DurationUnit, QuotaPeriod

SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{1,62}$"
# Only providers that have (or will get, in phase 3) an adapter can be registered as models.
SUPPORTED_PROVIDERS = frozenset(
    {LLMProviderName.DEEPSEEK, LLMProviderName.OPENAI, LLMProviderName.GEMINI}
)
MAX_BULLETS = 12


def _validate_strategies(names: list[str]) -> list[str]:
    valid = {s.value for s in StrategyName}
    unknown = [n for n in names if n not in valid]
    if unknown:
        raise ValueError(f"Unknown strategies: {', '.join(unknown)}.")
    return sorted(set(names))


def _validate_https(url: str | None) -> str | None:
    if url is not None and not url.startswith("https://"):
        raise ValueError("purchase_url must be an https:// URL.")
    return url


def _validate_bullets(bullets: list[str]) -> list[str]:
    if len(bullets) > MAX_BULLETS:
        raise ValueError(f"At most {MAX_BULLETS} bullets.")
    for bullet in bullets:
        if not bullet.strip() or len(bullet) > 200:
            raise ValueError("Each bullet must be 1-200 characters.")
        for _, field_name, spec, conversion in Formatter().parse(bullet):
            if field_name is None:
                continue
            if field_name not in FEATURES_BY_KEY or spec or conversion:
                raise ValueError(
                    f"Unknown placeholder {{{field_name}}}; use a plain feature key such as "
                    "{links_max}."
                )
    return bullets


# ---------- features / plans ----------


class FeatureResponse(BaseModel):
    key: str
    kind: str
    value_type: str
    default_period: str | None
    description: str


class PlanFeatureValue(BaseModel):
    int_value: int | None = None
    bool_value: bool | None = None
    period: QuotaPeriod | None = None


class PlanAdminResponse(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    tagline: str
    is_active: bool
    is_public: bool
    is_default_free: bool
    sort_order: int
    price_minor: int
    currency: str
    duration_unit: DurationUnit
    duration_count: int
    external_product_ref: str | None
    trial_days: int
    purchase_url: str | None
    allowed_strategies: list[str]
    badge_text: str | None
    cta_label: str
    footnote: str
    marketing_bullets: list[str]
    features: dict[str, PlanFeatureValue]
    model_ids: list[uuid.UUID]
    active_subscribers: int
    created_at: datetime
    updated_at: datetime


class _PlanFields(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    tagline: str = Field(default="", max_length=255)
    is_active: bool = True
    is_public: bool = True
    sort_order: int = 0
    price_minor: int = Field(default=0, ge=0)
    currency: str = Field(default="BDT", pattern=r"^[A-Z]{3}$")
    duration_unit: DurationUnit = DurationUnit.LIFETIME
    duration_count: int = Field(default=0, ge=0)
    external_product_ref: str | None = Field(default=None, max_length=128)
    trial_days: int = Field(default=0, ge=0, le=365)
    purchase_url: str | None = Field(default=None, max_length=500)
    allowed_strategies: list[str] = Field(
        default_factory=lambda: [StrategyName.SUPPORT_KORI_LICENSE.value]
    )
    badge_text: str | None = Field(default=None, max_length=50)
    cta_label: str = Field(default="", max_length=100)
    footnote: str = Field(default="", max_length=255)
    marketing_bullets: list[str] = Field(default_factory=list)

    @field_validator("marketing_bullets")
    @classmethod
    def _bullets(cls, value: list[str]) -> list[str]:
        return _validate_bullets(value)

    @field_validator("purchase_url")
    @classmethod
    def _purchase_url(cls, value: str | None) -> str | None:
        return _validate_https(value)

    @field_validator("allowed_strategies")
    @classmethod
    def _strategies(cls, value: list[str]) -> list[str]:
        return _validate_strategies(value)


def _check_duration(unit: DurationUnit, count: int) -> None:
    if unit == DurationUnit.LIFETIME and count != 0:
        raise ValueError("A lifetime plan must have duration_count 0.")
    if unit != DurationUnit.LIFETIME and count < 1:
        raise ValueError("duration_count must be at least 1.")


class PlanCreateRequest(_PlanFields):
    slug: str = Field(pattern=SLUG_PATTERN)
    # Copies feature values + allowed models from this plan (by slug) as a starting point.
    clone_from: str | None = None

    @model_validator(mode="after")
    def _duration(self) -> Self:
        _check_duration(self.duration_unit, self.duration_count)
        return self


class PlanUpdateRequest(BaseModel):
    """Partial update. The slug is immutable (grants and Kori mappings refer to it)."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    tagline: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    is_public: bool | None = None
    sort_order: int | None = None
    price_minor: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    duration_unit: DurationUnit | None = None
    duration_count: int | None = Field(default=None, ge=0)
    external_product_ref: str | None = Field(default=None, max_length=128)
    trial_days: int | None = Field(default=None, ge=0, le=365)
    purchase_url: str | None = Field(default=None, max_length=500)
    allowed_strategies: list[str] | None = None
    badge_text: str | None = Field(default=None, max_length=50)
    cta_label: str | None = Field(default=None, max_length=100)
    footnote: str | None = Field(default=None, max_length=255)
    marketing_bullets: list[str] | None = None

    @field_validator("marketing_bullets")
    @classmethod
    def _bullets(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _validate_bullets(value)

    @field_validator("purchase_url")
    @classmethod
    def _purchase_url(cls, value: str | None) -> str | None:
        return _validate_https(value)

    @field_validator("allowed_strategies")
    @classmethod
    def _strategies(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _validate_strategies(value)


class PlanFeaturesUpdateRequest(BaseModel):
    """Upsert for the listed features only; omitted features keep their current value."""

    features: dict[str, PlanFeatureValue] = Field(min_length=1)


class PlanModelsUpdateRequest(BaseModel):
    model_ids: list[uuid.UUID]


# ---------- models ----------


class LLMModelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: LLMProviderName
    model_id: str
    display_name: str
    is_active: bool
    # Read-only: this row is the same model the .env configures as the system default.
    is_fallback: bool = False
    supports_json_mode: bool
    cost_in_per_1m: Decimal | None
    cost_out_per_1m: Decimal | None
    sort_order: int
    # Whether this provider's API key is configured on the server — an active model whose key is
    # missing cannot actually serve requests.
    provider_configured: bool = False


class LLMModelCreateRequest(BaseModel):
    provider: LLMProviderName
    model_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=100)
    is_active: bool = True
    supports_json_mode: bool = True
    cost_in_per_1m: Decimal | None = Field(default=None, ge=0)
    cost_out_per_1m: Decimal | None = Field(default=None, ge=0)
    sort_order: int = 0

    @field_validator("provider")
    @classmethod
    def _supported(cls, value: LLMProviderName) -> LLMProviderName:
        if value not in SUPPORTED_PROVIDERS:
            raise ValueError(
                "Supported providers: " + ", ".join(sorted(p.value for p in SUPPORTED_PROVIDERS))
            )
        return value


class LLMModelUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None
    supports_json_mode: bool | None = None
    cost_in_per_1m: Decimal | None = Field(default=None, ge=0)
    cost_out_per_1m: Decimal | None = Field(default=None, ge=0)
    sort_order: int | None = None


# ---------- per-user subscription grants and overrides ----------


class SubscriptionAdminResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plan_slug: str
    plan_name: str
    status: str
    source: str
    starts_at: datetime
    expires_at: datetime | None


class UserSubscriptionResponse(BaseModel):
    current: SubscriptionAdminResponse | None
    history: list[SubscriptionAdminResponse]


class SubscriptionGrantRequest(BaseModel):
    plan_slug: str = Field(pattern=SLUG_PATTERN)
    # Optional explicit end date; omitted = the plan's own duration. Must be in the future.
    expires_at: datetime | None = None
    reason: str = Field(min_length=3, max_length=500)


class OverrideSetRequest(BaseModel):
    int_value: int | None = None
    bool_value: bool | None = None
    expires_at: datetime | None = None
    reason: str = Field(min_length=3, max_length=500)


class OverrideResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    feature_key: str
    int_value: int | None
    bool_value: bool | None
    expires_at: datetime | None
    reason: str
    granted_by: uuid.UUID | None
    created_at: datetime


class BillingSettingsResponse(BaseModel):
    enabled_strategies: list[str]
    max_activation_delay_days: int | None
    # Strategies that exist in code, so the admin UI can show what can be switched on.
    available_strategies: list[str]


class BillingSettingsUpdateRequest(BaseModel):
    enabled_strategies: list[str]
    max_activation_delay_days: int | None = Field(default=None, ge=1, le=3650)


class OrderAdminResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_email: str
    plan_slug: str
    strategy: str
    status: str
    amount_minor: int
    currency: str
    purchased_at: datetime | None
    created_at: datetime


class LicenseAdminResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_email: str
    plan_slug: str
    key_last4: str
    status: str
    validated_at: datetime
    last_revalidated_at: datetime | None
