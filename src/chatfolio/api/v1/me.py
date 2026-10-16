from fastapi import APIRouter

from chatfolio.api.deps import CurrentUserDep, EntitlementServiceDep
from chatfolio.entitlements.catalog import FEATURES, UNLIMITED
from chatfolio.schemas.billing import (
    EntitlementsResponse,
    FeatureEntitlementResponse,
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
