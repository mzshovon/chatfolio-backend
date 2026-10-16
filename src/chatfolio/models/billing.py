import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from chatfolio.config.settings import LLMProviderName
from chatfolio.db.base import Base
from chatfolio.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class FeatureKind(StrEnum):
    """How a feature is enforced — see docs/PRICING_ENTITLEMENTS_PLAN.md §3."""

    STOCK = "stock"  # counted from real rows (links, domains)
    FLOW = "flow"  # consumed over a period (chats, meetings, tokens, publishes)
    SESSION = "session"  # cap inside one chat session (messages per session)
    WINDOW = "window"  # read-time filter (chat history days)
    GATE = "gate"  # on/off


class FeatureValueType(StrEnum):
    INT = "int"
    BOOL = "bool"


class QuotaPeriod(StrEnum):
    LIFETIME = "lifetime"
    MONTHLY = "monthly"
    DAILY = "daily"


class DurationUnit(StrEnum):
    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    LIFETIME = "lifetime"


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class SubscriptionSource(StrEnum):
    ADMIN_GRANT = "admin_grant"
    TRIAL = "trial"
    SUPPORT_KORI_LICENSE = "support_kori_license"
    NATIVE_GATEWAY = "native_gateway"


# Non-native enums: a plain VARCHAR in Postgres, so adding a member later is a code change, not an
# `ALTER TYPE` migration.
def _enum(enum_cls: type[StrEnum], length: int = 32) -> Enum:
    return Enum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda e: [member.value for member in e],
    )


class Feature(Base):
    """Code-defined catalogue of everything a plan can grant. Seeded/kept in sync by
    `entitlements.catalog.ensure_catalog`; admins edit *values per plan*, never this table."""

    __tablename__ = "features"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[FeatureKind] = mapped_column(_enum(FeatureKind))
    value_type: Mapped[FeatureValueType] = mapped_column(_enum(FeatureValueType))
    default_period: Mapped[QuotaPeriod | None] = mapped_column(_enum(QuotaPeriod), default=None)
    description: Mapped[str] = mapped_column(Text, default="")


class Plan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plans"
    __table_args__ = (
        # At most one default free plan; a partial unique index is the only way to say that.
        Index(
            "uq_plans_single_default_free",
            "is_default_free",
            unique=True,
            postgresql_where="is_default_free",
        ),
    )

    slug: Mapped[str] = mapped_column(String(63), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    tagline: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default_free: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    # Minor units (500 BDT = 50000): avoids floats, and fits currencies with other exponents.
    price_minor: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="BDT")
    duration_unit: Mapped[DurationUnit] = mapped_column(
        _enum(DurationUnit), default=DurationUnit.LIFETIME
    )
    duration_count: Mapped[int] = mapped_column(Integer, default=0)
    # Support Kori product_id for this plan; None falls back to SUPPORT_KORI_PRODUCT_ID (phase 5).
    external_product_ref: Mapped[str | None] = mapped_column(String(128), default=None)
    trial_days: Mapped[int] = mapped_column(Integer, default=0)

    # Pricing-card copy. `marketing_bullets` are templates like "Up to {links_max} links",
    # rendered from this plan's real feature values so copy cannot drift from what is enforced.
    badge_text: Mapped[str | None] = mapped_column(String(50), default=None)
    cta_label: Mapped[str] = mapped_column(String(100), default="")
    footnote: Mapped[str] = mapped_column(String(255), default="")
    marketing_bullets: Mapped[list[str]] = mapped_column(JSON, default=list)

    features: Mapped[list["PlanFeature"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", lazy="selectin"
    )


class PlanFeature(Base):
    """One feature's value for one plan. `int_value == -1` means unlimited (see UNLIMITED)."""

    __tablename__ = "plan_features"

    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True
    )
    feature_key: Mapped[str] = mapped_column(
        String(64), ForeignKey("features.key", ondelete="CASCADE"), primary_key=True
    )
    int_value: Mapped[int | None] = mapped_column(Integer, default=None)
    bool_value: Mapped[bool | None] = mapped_column(Boolean, default=None)
    period: Mapped[QuotaPeriod | None] = mapped_column(_enum(QuotaPeriod), default=None)

    plan: Mapped[Plan] = relationship(back_populates="features")


class Subscription(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user's paid/trial/granted plan. Free is the *absence* of an active subscription — there is
    deliberately no row per free user, so signup stays a single insert and nothing needs
    backfilling when a user is created."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        Index(
            "uq_subscriptions_one_active_per_user",
            "user_id",
            unique=True,
            postgresql_where="status = 'active'",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plans.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        _enum(SubscriptionStatus), default=SubscriptionStatus.ACTIVE
    )
    source: Mapped[SubscriptionSource] = mapped_column(_enum(SubscriptionSource))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # None = never expires (lifetime plans). Expiry is always evaluated at read time against this
    # column; a cron that flips `status` is housekeeping, never the source of truth.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    plan: Mapped[Plan] = relationship(lazy="joined")


class EntitlementOverride(UUIDPrimaryKeyMixin, Base):
    """Per-user admin exception ("give this user 10 links"). Wins over the plan's value."""

    __tablename__ = "entitlement_overrides"
    __table_args__ = (UniqueConstraint("user_id", "feature_key", name="uq_override_user_feature"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    feature_key: Mapped[str] = mapped_column(
        String(64), ForeignKey("features.key", ondelete="CASCADE")
    )
    int_value: Mapped[int | None] = mapped_column(Integer, default=None)
    bool_value: Mapped[bool | None] = mapped_column(Boolean, default=None)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    reason: Mapped[str] = mapped_column(String(500), default="")
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LLMModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A selectable AI model. Metadata only — API keys stay in env/secret storage (LLMSettings),
    never in this table. Exactly one active row is the fallback (DeepSeek by default): the model
    free users get and what a failing/deactivated choice falls back to."""

    __tablename__ = "llm_models"
    __table_args__ = (
        UniqueConstraint("provider", "model_id", name="uq_llm_models_provider_model"),
        Index(
            "uq_llm_models_single_fallback",
            "is_fallback",
            unique=True,
            postgresql_where="is_fallback",
        ),
    )

    provider: Mapped[LLMProviderName] = mapped_column(_enum(LLMProviderName))
    model_id: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_json_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    # USD per 1M tokens, for cost reporting only.
    cost_in_per_1m: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), default=None)
    cost_out_per_1m: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), default=None)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class PlanModel(Base):
    """Which models a plan may use. The fallback model is always usable regardless of this table."""

    __tablename__ = "plan_models"

    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("llm_models.id", ondelete="CASCADE"), primary_key=True
    )


class UserModelPreference(Base):
    """The model a user chose (only honoured while their plan allows model selection and the model
    is active and in the plan's allowed set — checked on every request, never trusted from here)."""

    __tablename__ = "user_model_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("llm_models.id", ondelete="SET NULL"), default=None
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
