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

# Id of the synthetic "system default" entry (it is configuration, not a DB row).
DEFAULT_MODEL_ID = "default"


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

    1. plan doesn't grant `model_selection`            -> the default model
    2. user's choice is active, in the plan's set, and its provider has an API key -> that model
    3. anything else (no choice, deactivated, removed from the plan, key missing)  -> the default

    The default (and failure fallback) is the **env-configured** provider — LLM_DEFAULT_PROVIDER
    with its LLM_<PROVIDER>_MODEL, API key and base URL — never a row in the database or a
    constant in code. Only the *extra* models premium users may choose between live in the DB.

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
        default = self.default_model()
        entitlements = await self._entitlements.get(user_id)
        if not entitlements.enabled(FeatureKey.MODEL_SELECTION):
            return default

        preference = await self._session.get(UserModelPreference, user_id)
        if preference is None or preference.model_id is None:
            return default
        catalog = await self._catalog()
        chosen = catalog["models"].get(str(preference.model_id))
        if chosen is not None and self._is_default(chosen):
            return default
        allowed = str(preference.model_id) in catalog["plans"].get(entitlements.plan_slug, [])
        if chosen is None or not allowed or not self._configured(chosen):
            logger.info(
                "llm.preference_not_usable",
                user_id=str(user_id),
                model_id=str(preference.model_id),
            )
            return default
        return _to_resolved(chosen)

    async def fallback(self) -> ResolvedModel:
        return self.default_model()

    def default_model(self) -> ResolvedModel:
        """The env-configured default/fallback model."""
        provider = self._llm_settings.default_provider
        model = self._llm_settings.model_for(provider)
        if self._llm_settings.base_url_for(provider) is None or model is None:
            # Misconfiguration (unsupported provider, or LLM_<PROVIDER>_MODEL unset): fail loudly
            # rather than guess a model.
            logger.error("llm.default_model_not_configured", provider=provider.value)
            raise ServiceUnavailableError()
        return ResolvedModel(
            id=DEFAULT_MODEL_ID,
            provider=provider,
            model_id=model,
            display_name=f"{provider.value} ({model})",
            supports_json_mode=True,
            is_fallback=True,
        )

    def _is_default(self, raw: dict[str, Any]) -> bool:
        default = self.default_model()
        return bool(
            raw["provider"] == default.provider.value and raw["model_id"] == default.model_id
        )

    async def choices_for(self, user_id: uuid.UUID) -> ModelChoices:
        catalog = await self._catalog()
        entitlements = await self._entitlements.get(user_id)
        enabled = entitlements.enabled(FeatureKey.MODEL_SELECTION)
        plan_models = set(catalog["plans"].get(entitlements.plan_slug, []))

        default = self.default_model()
        choices = [ModelChoice(default, True, None)]  # always available to everyone
        for raw in catalog["models"].values():
            if self._is_default(raw):
                continue  # the same model is already the first entry
            in_plan = raw["id"] in plan_models
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
                        is_fallback=False,
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
