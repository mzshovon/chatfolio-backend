from fastapi import APIRouter, Request, status

from chatfolio.api.deps import BillingServiceDep, CurrentUserDep
from chatfolio.core.rate_limit import limiter
from chatfolio.schemas.admin_billing import UserSubscriptionResponse
from chatfolio.schemas.billing import (
    ActivateLicenseRequest,
    StartTrialRequest,
    StrategiesResponse,
)

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/strategies", response_model=StrategiesResponse)
async def get_strategies(
    current_user: CurrentUserDep, service: BillingServiceDep
) -> StrategiesResponse:
    """How the signed-in user can get a plan right now: the enabled activation methods with the
    plans each sells (and where to buy), plus any free trial they may still start."""
    return StrategiesResponse.model_validate(await service.list_strategies(current_user))


@router.get("/subscription", response_model=UserSubscriptionResponse)
async def get_subscription(
    current_user: CurrentUserDep, service: BillingServiceDep
) -> UserSubscriptionResponse:
    return await service.get_state(current_user.id)


@router.post(
    "/activate", response_model=UserSubscriptionResponse, status_code=status.HTTP_201_CREATED
)
# Per-IP on top of the per-account wrong-key budget inside the service.
@limiter.limit("10/hour")
async def activate_license(
    request: Request,
    payload: ActivateLicenseRequest,
    current_user: CurrentUserDep,
    service: BillingServiceDep,
) -> UserSubscriptionResponse:
    """Activate a plan with a Support Kori license key. The plan comes from the server's own
    mapping (and Support Kori's answer), never from what the client claims it paid for."""
    return await service.activate_license(current_user, payload.license_key, payload.plan_slug)


@router.post("/trial", response_model=UserSubscriptionResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/hour")
async def start_trial(
    request: Request,
    payload: StartTrialRequest,
    current_user: CurrentUserDep,
    service: BillingServiceDep,
) -> UserSubscriptionResponse:
    return await service.start_trial(current_user, payload.plan_slug)
