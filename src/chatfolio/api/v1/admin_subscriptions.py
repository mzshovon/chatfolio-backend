import uuid

from fastapi import APIRouter, status

from chatfolio.api.deps import AdminUserDep, DbSessionDep, EntitlementCacheDep
from chatfolio.schemas.admin_billing import (
    OverrideResponse,
    OverrideSetRequest,
    SubscriptionGrantRequest,
    UserSubscriptionResponse,
)
from chatfolio.services.subscription_admin_service import SubscriptionAdminService

router = APIRouter(prefix="/admin/users/{user_id}", tags=["admin-pricing"])


def _service(session: DbSessionDep, cache: EntitlementCacheDep) -> SubscriptionAdminService:
    return SubscriptionAdminService(session, cache)


@router.get("/subscription", response_model=UserSubscriptionResponse)
async def get_subscription(
    user_id: uuid.UUID,
    current_user: AdminUserDep,
    session: DbSessionDep,
    cache: EntitlementCacheDep,
) -> UserSubscriptionResponse:
    return await _service(session, cache).get_subscriptions(user_id)


@router.post(
    "/subscription", response_model=UserSubscriptionResponse, status_code=status.HTTP_201_CREATED
)
async def grant_subscription(
    user_id: uuid.UUID,
    payload: SubscriptionGrantRequest,
    current_user: AdminUserDep,
    session: DbSessionDep,
    cache: EntitlementCacheDep,
) -> UserSubscriptionResponse:
    return await _service(session, cache).grant(current_user, user_id, payload)


@router.delete("/subscription", response_model=UserSubscriptionResponse)
async def revoke_subscription(
    user_id: uuid.UUID,
    current_user: AdminUserDep,
    session: DbSessionDep,
    cache: EntitlementCacheDep,
) -> UserSubscriptionResponse:
    return await _service(session, cache).revoke(current_user, user_id)


@router.get("/overrides", response_model=list[OverrideResponse])
async def list_overrides(
    user_id: uuid.UUID,
    current_user: AdminUserDep,
    session: DbSessionDep,
    cache: EntitlementCacheDep,
) -> list[OverrideResponse]:
    overrides = await _service(session, cache).list_overrides(user_id)
    return [OverrideResponse.model_validate(o) for o in overrides]


@router.put("/overrides/{feature_key}", response_model=OverrideResponse)
async def set_override(
    user_id: uuid.UUID,
    feature_key: str,
    payload: OverrideSetRequest,
    current_user: AdminUserDep,
    session: DbSessionDep,
    cache: EntitlementCacheDep,
) -> OverrideResponse:
    override = await _service(session, cache).set_override(
        current_user, user_id, feature_key, payload
    )
    return OverrideResponse.model_validate(override)


@router.delete("/overrides/{feature_key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_override(
    user_id: uuid.UUID,
    feature_key: str,
    current_user: AdminUserDep,
    session: DbSessionDep,
    cache: EntitlementCacheDep,
) -> None:
    await _service(session, cache).delete_override(current_user, user_id, feature_key)
