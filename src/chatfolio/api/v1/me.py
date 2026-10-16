from fastapi import APIRouter

from chatfolio.api.deps import CurrentUserDep, EntitlementServiceDep, ModelRouterDep
from chatfolio.entitlements.catalog import FEATURES, UNLIMITED
from chatfolio.llm.model_router import ModelChoices
from chatfolio.schemas.billing import (
    EntitlementsResponse,
    FeatureEntitlementResponse,
    ModelChoiceResponse,
    ModelPreferenceRequest,
    MyModelsResponse,
    PlanSummaryResponse,
)

router = APIRouter(prefix="/me", tags=["me"])


@router.get("/entitlements", response_model=EntitlementsResponse)
async def get_my_entitlements(
    current_user: CurrentUserDep, service: EntitlementServiceDep
) -> EntitlementsResponse:
    """What the signed-in user's plan grants. Drives UI locking only — the server enforces every
    limit itself, so a tampered client gains nothing."""
    entitlements = await service.get(current_user.id)
    features: dict[str, FeatureEntitlementResponse] = {}
    for definition in FEATURES:
        value = entitlements.values.get(definition.key.value)
        int_value = value.int_value if value else None
        features[definition.key.value] = FeatureEntitlementResponse(
            kind=definition.kind.value,
            value_type=definition.value_type.value,
            int_value=int_value,
            bool_value=value.bool_value if value else None,
            unlimited=int_value == UNLIMITED,
            period=value.period.value if value and value.period else None,
        )
    return EntitlementsResponse(
        plan=PlanSummaryResponse(slug=entitlements.plan_slug, name=entitlements.plan_name),
        source=entitlements.source,
        expires_at=entitlements.subscription_expires_at,
        features=features,
    )


@router.get("/models", response_model=MyModelsResponse)
async def get_my_models(
    current_user: CurrentUserDep, model_router: ModelRouterDep
) -> MyModelsResponse:
    """Every active model with whether this user may select it (locked ones show why), plus the
    model that will actually answer right now."""
    choices = await model_router.choices_for(current_user.id)
    return _to_models_response(choices)


@router.put("/models/preference", response_model=MyModelsResponse)
async def set_my_model_preference(
    payload: ModelPreferenceRequest, current_user: CurrentUserDep, model_router: ModelRouterDep
) -> MyModelsResponse:
    await model_router.set_preference(current_user.id, payload.model_id)
    return _to_models_response(await model_router.choices_for(current_user.id))


def _to_models_response(choices: ModelChoices) -> MyModelsResponse:
    return MyModelsResponse(
        selection_enabled=choices.selection_enabled,
        selected_model_id=choices.selected_model_id,
        effective_model_id=choices.effective_model_id,
        models=[
            ModelChoiceResponse(
                id=c.model.id,
                provider=c.model.provider.value,
                model_id=c.model.model_id,
                display_name=c.model.display_name,
                is_default=c.model.is_fallback,
                allowed=c.allowed,
                locked_reason=c.locked_reason,
            )
            for c in choices.choices
        ],
    )
