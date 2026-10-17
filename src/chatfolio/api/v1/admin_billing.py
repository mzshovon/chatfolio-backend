import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from chatfolio.api.deps import (
    AdminUserDep,
    BillingServiceDep,
    BillingSettingsServiceDep,
    DbSessionDep,
)
from chatfolio.billing.strategy import IMPLEMENTED_STRATEGIES
from chatfolio.models.billing import License, Order, Plan
from chatfolio.models.user import User
from chatfolio.schemas.admin_billing import (
    BillingSettingsResponse,
    BillingSettingsUpdateRequest,
    LicenseAdminResponse,
    OrderAdminResponse,
)
from chatfolio.services.billing_settings_service import BillingSettings

router = APIRouter(prefix="/admin/billing", tags=["admin-pricing"])


def _settings_response(settings: BillingSettings) -> BillingSettingsResponse:
    return BillingSettingsResponse(
        enabled_strategies=settings.enabled_strategies,
        max_activation_delay_days=settings.max_activation_delay_days,
        available_strategies=sorted(s.value for s in IMPLEMENTED_STRATEGIES),
    )


@router.get("/settings", response_model=BillingSettingsResponse)
async def get_billing_settings(
    current_user: AdminUserDep, service: BillingSettingsServiceDep
) -> BillingSettingsResponse:
    return _settings_response(await service.get())


@router.put("/settings", response_model=BillingSettingsResponse)
async def update_billing_settings(
    payload: BillingSettingsUpdateRequest,
    current_user: AdminUserDep,
    service: BillingSettingsServiceDep,
) -> BillingSettingsResponse:
    updated = await service.update(
        current_user,
        enabled_strategies=payload.enabled_strategies,
        max_activation_delay_days=payload.max_activation_delay_days,
    )
    return _settings_response(updated)


@router.get("/orders", response_model=list[OrderAdminResponse])
async def list_orders(
    current_user: AdminUserDep,
    session: DbSessionDep,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[OrderAdminResponse]:
    stmt = (
        select(Order, User.email, Plan.slug)
        .join(User, User.id == Order.user_id)
        .join(Plan, Plan.id == Order.plan_id)
        .order_by(Order.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if user_id is not None:
        stmt = stmt.where(Order.user_id == user_id)
    return [
        OrderAdminResponse(
            id=o.id,
            user_id=o.user_id,
            user_email=email,
            plan_slug=slug,
            strategy=o.strategy,
            status=o.status.value,
            amount_minor=o.amount_minor,
            currency=o.currency,
            purchased_at=o.purchased_at,
            created_at=o.created_at,
        )
        for o, email, slug in (await session.execute(stmt)).all()
    ]


@router.get("/licenses", response_model=list[LicenseAdminResponse])
async def list_licenses(
    current_user: AdminUserDep,
    session: DbSessionDep,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[LicenseAdminResponse]:
    """License keys are never returned — only the last 4 characters, for support lookups."""
    stmt = (
        select(License, User.email, Plan.slug)
        .join(User, User.id == License.user_id)
        .join(Plan, Plan.id == License.plan_id)
        .order_by(License.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if user_id is not None:
        stmt = stmt.where(License.user_id == user_id)
    return [
        LicenseAdminResponse(
            id=lic.id,
            user_id=lic.user_id,
            user_email=email,
            plan_slug=slug,
            key_last4=lic.key_last4,
            status=lic.status.value,
            validated_at=lic.validated_at,
            last_revalidated_at=lic.last_revalidated_at,
        )
        for lic, email, slug in (await session.execute(stmt)).all()
    ]


@router.post("/licenses/{license_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_license(
    license_id: uuid.UUID, current_user: AdminUserDep, service: BillingServiceDep
) -> None:
    """Ends the license and the subscription it created; the user returns to the free plan."""
    await service.admin_revoke_license(current_user, license_id)
