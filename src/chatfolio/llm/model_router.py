import uuid
from dataclasses import asdict, dataclass
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.config.settings import LLMProviderName, LLMSettings
from chatfolio.core.exceptions import (
    FeatureNotInPlanError,
    ModelNotAllowedError,
    NotFoundError,
    ServiceUnavailableError,
)
from chatfolio.entitlements.cache import EntitlementCache
from chatfolio.entitlements.catalog import FeatureKey
from chatfolio.models.billing import LLMModel, Plan, PlanModel, UserModelPreference
from chatfolio.services.entitlement_service import EntitlementService

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ResolvedModel:
    id: str
    provider: LLMProviderName
    model_id: str
    display_name: str
    supports_json_mode: bool
    is_fallback: bool


@dataclass(frozen=True)
class ModelChoice:
    model: ResolvedModel
    allowed: bool
    # None when allowed; "plan" = not in this plan, "unavailable" = provider not configured.
    locked_reason: str | None


@dataclass(frozen=True)
class ModelChoices:
    selection_enabled: bool
    selected_model_id: str | None
    effective_model_id: str
    choices: list[ModelChoice]


class ModelRouter:
    """Decides which AI model answers for a given user — the one place that rule lives.

    1. plan doesn't grant `model_selection`            -> the fallback model
    2. user's choice is active, in the plan's set, and its provider has an API key -> that model
    3. anything else (no choice, deactivated, removed from the plan, key missing)  -> the fallback

    Evaluated on every call from live data, so an admin change (deactivating a model, editing a
    plan) or an expired subscription takes effect immediately; the stored preference is never
    trusted on its own.
    """

    def __init__(
        self,
        session: AsyncSession,
        entitlements: EntitlementService,
        cache: EntitlementCache,
        llm_settings: LLMSettings,
    ) -> None:
        self._session = session
        self._entitlements = entitlements
        self._cache = cache
        self._llm_settings = llm_settings

    async def resolve(self, user_id: uuid.UUID) -> ResolvedModel:
        catalog = await self._catalog()
        fallback = _fallback(catalog)
        entitlements = await self._entitlements.get(user_id)
        if not entitlements.enabled(FeatureKey.MODEL_SELECTION):
            return fallback

        preference = await self._session.get(UserModelPreference, user_id)
        if preference is None or preference.model_id is None:
            return fallback
        chosen = catalog["models"].get(str(preference.model_id))
        allowed = str(preference.model_id) in catalog["plans"].get(entitlements.plan_slug, [])
        if chosen is None or not allowed or not self._configured(chosen):
            logger.info(
                "llm.preference_not_usable",
                user_id=str(user_id),
                model_id=str(preference.model_id),
            )
            return fallback
        return _to_resolved(chosen)

    async def fallback(self) -> ResolvedModel:
        return _fallback(await self._catalog())

    async def choices_for(self, user_id: uuid.UUID) -> ModelChoices:
        catalog = await self._catalog()
        entitlements = await self._entitlements.get(user_id)
        enabled = entitlements.enabled(FeatureKey.MODEL_SELECTION)
        plan_models = set(catalog["plans"].get(entitlements.plan_slug, []))

        choices = []
        for raw in catalog["models"].values():
            in_plan = raw["id"] in plan_models or raw["is_fallback"]
            configured = self._configured(raw)
            reason = (
                None if (in_plan and configured) else ("plan" if not in_plan else "unavailable")
            )
            choices.append(ModelChoice(_to_resolved(raw), reason is None, reason))
        preference = await self._session.get(UserModelPreference, user_id)
        effective = await self.resolve(user_id)
        return ModelChoices(
            selection_enabled=enabled,
            selected_model_id=str(preference.model_id)
            if preference and preference.model_id
            else None,
            effective_model_id=effective.id,
            choices=choices,
        )

    async def set_preference(self, user_id: uuid.UUID, model_id: uuid.UUID | None) -> None:
        entitlements = await self._entitlements.get(user_id)
        if not entitlements.enabled(FeatureKey.MODEL_SELECTION):
            raise FeatureNotInPlanError(FeatureKey.MODEL_SELECTION.value)

        if model_id is not None:
            catalog = await self._catalog()
            chosen = catalog["models"].get(str(model_id))
            if chosen is None:
                raise NotFoundError("Model not found.")
            if (
                str(model_id) not in catalog["plans"].get(entitlements.plan_slug, [])
                and not chosen["is_fallback"]
            ):
                raise ModelNotAllowedError()
            if not self._configured(chosen):
                raise ModelNotAllowedError("This model is not available right now.")

        preference = await self._session.get(UserModelPreference, user_id)
        if preference is None:
            preference = UserModelPreference(user_id=user_id)
            self._session.add(preference)
        preference.model_id = model_id
        await self._session.flush()

    # ---- catalogue ----

    def _configured(self, raw: dict[str, Any]) -> bool:
        return self._llm_settings.api_key_for(LLMProviderName(raw["provider"])) is not None

    async def _catalog(self) -> dict[str, Any]:
        cached = await self._cache.get_catalog("models")
        if cached is not None:
            return cached  # type: ignore[no-any-return]

        models = (
            (await self._session.execute(select(LLMModel).where(LLMModel.is_active.is_(True))))
            .scalars()
            .all()
        )
        links = (
            await self._session.execute(
                select(Plan.slug, PlanModel.model_id).join(PlanModel, PlanModel.plan_id == Plan.id)
            )
        ).all()
        active_ids = {str(m.id) for m in models}
        plans: dict[str, list[str]] = {}
        for slug, model_id in links:
            if str(model_id) in active_ids:
                plans.setdefault(slug, []).append(str(model_id))
        catalog = {
            "models": {
                str(m.id): asdict(
                    ResolvedModel(
                        id=str(m.id),
                        provider=m.provider,
                        model_id=m.model_id,
                        display_name=m.display_name,
                        supports_json_mode=m.supports_json_mode,
                        is_fallback=m.is_fallback,
                    )
                )
                | {"provider": m.provider.value, "sort_order": m.sort_order}
                for m in sorted(models, key=lambda m: (m.sort_order, m.display_name))
            },
            "plans": plans,
        }
        await self._cache.set_catalog("models", catalog)
        return catalog


def _to_resolved(raw: dict[str, Any]) -> ResolvedModel:
    return ResolvedModel(
        id=raw["id"],
        provider=LLMProviderName(raw["provider"]),
        model_id=raw["model_id"],
        display_name=raw["display_name"],
        supports_json_mode=raw["supports_json_mode"],
        is_fallback=raw["is_fallback"],
    )


def _fallback(catalog: dict[str, Any]) -> ResolvedModel:
    for raw in catalog["models"].values():
        if raw["is_fallback"]:
            return _to_resolved(raw)
    # No active fallback means the catalogue is broken; fail loudly rather than guess a model.
    logger.error("llm.fallback_model_missing")
    raise ServiceUnavailableError()
