"""Code-defined feature catalogue plus the default plans seeded on a fresh database.

Features live in code because each one needs an enforcement hook; admins customise the *value per
plan*, never the list of features. `ensure_catalog` is idempotent: it keeps the `features` table in
sync with FEATURES and creates the default plans only if they are missing — it never overwrites a
plan an admin has already edited. Used by the Alembic seed migration and by the test suite (which
truncates every table between tests).
"""

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.config.settings import LLMProviderName
from chatfolio.models.billing import (
    DurationUnit,
    Feature,
    FeatureKind,
    FeatureValueType,
    LLMModel,
    Plan,
    PlanFeature,
    PlanModel,
    QuotaPeriod,
)

UNLIMITED = -1


class FeatureKey(StrEnum):
    LINKS_MAX = "links_max"
    PUBLISHES_LIMIT = "publishes_limit"
    CUSTOM_DOMAINS_MAX = "custom_domains_max"
    RECRUITER_CHATS_LIMIT = "recruiter_chats_limit"
    CHAT_MESSAGES_PER_SESSION = "chat_messages_per_session"
    AI_TOKENS_LIMIT = "ai_tokens_limit"
    CHAT_HISTORY_DAYS = "chat_history_days"
    RECRUITER_INSIGHTS = "recruiter_insights"
    CALENDAR_BOOKING = "calendar_booking"
    MEETINGS_LIMIT = "meetings_limit"
    REMOVE_BADGE = "remove_badge"
    MODEL_SELECTION = "model_selection"


@dataclass(frozen=True)
class FeatureDef:
    key: FeatureKey
    kind: FeatureKind
    value_type: FeatureValueType
    description: str
    default_period: QuotaPeriod | None = None


_INT = FeatureValueType.INT
_BOOL = FeatureValueType.BOOL

FEATURES: tuple[FeatureDef, ...] = (
    FeatureDef(
        FeatureKey.LINKS_MAX, FeatureKind.STOCK, _INT, "Aliases (slugs) of the user's chatfolio."
    ),
    FeatureDef(
        FeatureKey.PUBLISHES_LIMIT,
        FeatureKind.FLOW,
        _INT,
        "Number of publish actions allowed per period.",
        QuotaPeriod.LIFETIME,
    ),
    FeatureDef(
        FeatureKey.CUSTOM_DOMAINS_MAX, FeatureKind.STOCK, _INT, "Custom domains that can be added."
    ),
    FeatureDef(
        FeatureKey.RECRUITER_CHATS_LIMIT,
        FeatureKind.FLOW,
        _INT,
        "New recruiter chat sessions per period.",
        QuotaPeriod.MONTHLY,
    ),
    FeatureDef(
        FeatureKey.CHAT_MESSAGES_PER_SESSION,
        FeatureKind.SESSION,
        _INT,
        "Maximum messages inside a single recruiter chat session.",
    ),
    FeatureDef(
        FeatureKey.AI_TOKENS_LIMIT,
        FeatureKind.FLOW,
        _INT,
        "LLM token budget per period (cost safety net).",
        QuotaPeriod.MONTHLY,
    ),
    FeatureDef(
        FeatureKey.CHAT_HISTORY_DAYS,
        FeatureKind.WINDOW,
        _INT,
        "How many days back the owner can view recruiter conversations.",
    ),
    FeatureDef(
        FeatureKey.RECRUITER_INSIGHTS,
        FeatureKind.GATE,
        _BOOL,
        "Recruiter insights: who asked, what, and from where.",
    ),
    FeatureDef(
        FeatureKey.CALENDAR_BOOKING,
        FeatureKind.GATE,
        _BOOL,
        "Google Calendar / Meet booking.",
    ),
    FeatureDef(
        FeatureKey.MEETINGS_LIMIT,
        FeatureKind.FLOW,
        _INT,
        "Meetings that can be booked per period.",
        QuotaPeriod.LIFETIME,
    ),
    FeatureDef(FeatureKey.REMOVE_BADGE, FeatureKind.GATE, _BOOL, "Hide the Chatfolio badge."),
    FeatureDef(
        FeatureKey.MODEL_SELECTION,
        FeatureKind.GATE,
        _BOOL,
        "May choose which AI model answers recruiters.",
    ),
)

FEATURES_BY_KEY: dict[str, FeatureDef] = {f.key.value: f for f in FEATURES}


@dataclass(frozen=True)
class PlanFeatureSeed:
    int_value: int | None = None
    bool_value: bool | None = None
    period: QuotaPeriod | None = None


@dataclass(frozen=True)
class PlanSeed:
    slug: str
    name: str
    tagline: str
    sort_order: int
    price_minor: int
    duration_unit: DurationUnit
    duration_count: int
    cta_label: str
    footnote: str
    bullets: tuple[str, ...]
    features: dict[FeatureKey, PlanFeatureSeed]
    is_default_free: bool = False
    badge_text: str | None = None
    currency: str = "BDT"


def _i(value: int, period: QuotaPeriod | None = None) -> PlanFeatureSeed:
    return PlanFeatureSeed(int_value=value, period=period)


def _b(value: bool) -> PlanFeatureSeed:
    return PlanFeatureSeed(bool_value=value)


DEFAULT_PLANS: tuple[PlanSeed, ...] = (
    PlanSeed(
        slug="free",
        name="Free",
        tagline="Try Chatfolio and let your CV start talking.",
        sort_order=0,
        price_minor=0,
        duration_unit=DurationUnit.LIFETIME,
        duration_count=0,
        cta_label="Start free",
        footnote="No card needed.",
        bullets=(
            "{links_max} chatfolio link",
            "Up to {recruiter_chats_limit} recruiter chats a month",
            "{chat_history_days}-day chat history",
            "Calendar booking for up to {meetings_limit} meetings",
        ),
        is_default_free=True,
        features={
            FeatureKey.LINKS_MAX: _i(1),
            FeatureKey.PUBLISHES_LIMIT: _i(3, QuotaPeriod.LIFETIME),
            FeatureKey.CUSTOM_DOMAINS_MAX: _i(0),
            FeatureKey.RECRUITER_CHATS_LIMIT: _i(20, QuotaPeriod.MONTHLY),
            FeatureKey.CHAT_MESSAGES_PER_SESSION: _i(UNLIMITED),
            FeatureKey.AI_TOKENS_LIMIT: _i(200_000, QuotaPeriod.MONTHLY),
            FeatureKey.CHAT_HISTORY_DAYS: _i(7),
            FeatureKey.RECRUITER_INSIGHTS: _b(False),
            FeatureKey.CALENDAR_BOOKING: _b(True),
            FeatureKey.MEETINGS_LIMIT: _i(2, QuotaPeriod.LIFETIME),
            FeatureKey.REMOVE_BADGE: _b(False),
            FeatureKey.MODEL_SELECTION: _b(False),
        },
    ),
    PlanSeed(
        slug="premium",
        name="Premium",
        tagline="For active job seekers who want every lead.",
        sort_order=10,
        price_minor=50_000,
        duration_unit=DurationUnit.MONTH,
        duration_count=6,
        cta_label="Go Premium",
        footnote="Secure checkout by Support Kori · bKash, Nagad, cards",
        badge_text="MOST POPULAR",
        bullets=(
            "Up to {links_max} chatfolio links and custom domains",
            "Unlimited recruiter chats and AI answers",
            "Recruiter insights: who asked, what, and from where",
            "Calendar booking for Google Meet and calls",
            "Full chat history, no badge",
        ),
        features={
            FeatureKey.LINKS_MAX: _i(5),
            FeatureKey.PUBLISHES_LIMIT: _i(UNLIMITED, QuotaPeriod.LIFETIME),
            FeatureKey.CUSTOM_DOMAINS_MAX: _i(5),
            FeatureKey.RECRUITER_CHATS_LIMIT: _i(UNLIMITED, QuotaPeriod.MONTHLY),
            FeatureKey.CHAT_MESSAGES_PER_SESSION: _i(UNLIMITED),
            FeatureKey.AI_TOKENS_LIMIT: _i(5_000_000, QuotaPeriod.MONTHLY),
            FeatureKey.CHAT_HISTORY_DAYS: _i(UNLIMITED),
            FeatureKey.RECRUITER_INSIGHTS: _b(True),
            FeatureKey.CALENDAR_BOOKING: _b(True),
            FeatureKey.MEETINGS_LIMIT: _i(UNLIMITED, QuotaPeriod.LIFETIME),
            FeatureKey.REMOVE_BADGE: _b(True),
            FeatureKey.MODEL_SELECTION: _b(True),
        },
    ),
)


def validate_feature_value(
    definition: FeatureDef,
    int_value: int | None,
    bool_value: bool | None,
    period: QuotaPeriod | None,
) -> str | None:
    """Returns a human-readable problem, or None if the value is acceptable for this feature."""
    if definition.value_type == FeatureValueType.INT:
        if int_value is None or bool_value is not None:
            return f"{definition.key.value} takes an integer (use -1 for unlimited)."
        if int_value < UNLIMITED:
            return f"{definition.key.value} cannot be below -1."
    else:
        if bool_value is None or int_value is not None:
            return f"{definition.key.value} takes true/false."
    if period is not None and definition.kind != FeatureKind.FLOW:
        return f"{definition.key.value} does not use a period."
    return None


@dataclass(frozen=True)
class ModelSeed:
    provider: LLMProviderName
    model_id: str
    display_name: str
    is_fallback: bool = False
    sort_order: int = 0


# Only the fallback is seeded: other providers need an API key and a deliberate admin decision
# (and current model ids), so admins add them through the admin API.
DEFAULT_MODELS: tuple[ModelSeed, ...] = (
    ModelSeed(LLMProviderName.DEEPSEEK, "deepseek-chat", "DeepSeek", is_fallback=True),
)


async def ensure_catalog(session: AsyncSession) -> None:
    """Idempotent. Syncs `features` to FEATURES; creates default plans only when missing."""
    existing_features = {f.key: f for f in (await session.execute(select(Feature))).scalars()}
    for definition in FEATURES:
        row = existing_features.get(definition.key.value)
        if row is None:
            session.add(
                Feature(
                    key=definition.key.value,
                    kind=definition.kind,
                    value_type=definition.value_type,
                    default_period=definition.default_period,
                    description=definition.description,
                )
            )
        else:
            row.kind = definition.kind
            row.value_type = definition.value_type
            row.default_period = definition.default_period
            row.description = definition.description
    await session.flush()

    fallback = await _ensure_fallback_model(session)

    existing_slugs = set((await session.execute(select(Plan.slug))).scalars())
    new_plans: list[Plan] = []
    for seed in DEFAULT_PLANS:
        if seed.slug in existing_slugs:
            continue
        plan = Plan(
            slug=seed.slug,
            name=seed.name,
            tagline=seed.tagline,
            sort_order=seed.sort_order,
            price_minor=seed.price_minor,
            currency=seed.currency,
            duration_unit=seed.duration_unit,
            duration_count=seed.duration_count,
            cta_label=seed.cta_label,
            footnote=seed.footnote,
            badge_text=seed.badge_text,
            marketing_bullets=list(seed.bullets),
            is_default_free=seed.is_default_free,
            features=[
                PlanFeature(
                    feature_key=key.value,
                    int_value=value.int_value,
                    bool_value=value.bool_value,
                    period=value.period,
                )
                for key, value in seed.features.items()
            ],
        )
        session.add(plan)
        new_plans.append(plan)
    await session.flush()

    # Only plans created *right now* get the fallback model: an existing plan may have been
    # restricted by an admin on purpose and must never be touched here.
    if fallback is not None:
        for plan in new_plans:
            session.add(PlanModel(plan_id=plan.id, model_id=fallback.id))
        await session.flush()


async def _ensure_fallback_model(session: AsyncSession) -> LLMModel | None:
    """Seeds the default models on an empty table; returns the current fallback (if any)."""
    if (await session.execute(select(LLMModel.id).limit(1))).first() is None:
        for seed in DEFAULT_MODELS:
            session.add(
                LLMModel(
                    provider=seed.provider,
                    model_id=seed.model_id,
                    display_name=seed.display_name,
                    is_fallback=seed.is_fallback,
                    sort_order=seed.sort_order,
                )
            )
        await session.flush()
    return (
        await session.execute(select(LLMModel).where(LLMModel.is_fallback.is_(True)))
    ).scalar_one_or_none()
