import uuid

from fastapi import APIRouter, status

from chatfolio.api.deps import CurrentUserDep, PortfolioServiceDep
from chatfolio.models.chatfolio import PublicChatfolio
from chatfolio.schemas.portfolio_settings import (
    AliasCreateRequest,
    AliasResponse,
    AliasUpdateRequest,
    PortfolioSettingsResponse,
    PortfolioSettingsUpdateRequest,
)
from chatfolio.services.portfolio_service import subdomain_for

router = APIRouter(prefix="/portfolio-settings", tags=["portfolio-settings"])


def _to_response(chatfolio: PublicChatfolio) -> PortfolioSettingsResponse:
    return PortfolioSettingsResponse(
        slug=chatfolio.slug,
        subdomain=subdomain_for(chatfolio.slug),
        previous_slug=chatfolio.previous_slug,
        is_published=chatfolio.is_published,
        published_at=chatfolio.published_at,
        contact_cta_config=chatfolio.contact_cta_config,
        cv_downloadable=chatfolio.cv_downloadable,
    )


@router.get("", response_model=PortfolioSettingsResponse)
async def get_portfolio_settings(
    current_user: CurrentUserDep, service: PortfolioServiceDep
) -> PortfolioSettingsResponse:
    return _to_response(await service.get_or_create_for_user(current_user))


@router.patch("", response_model=PortfolioSettingsResponse)
async def update_portfolio_settings(
    payload: PortfolioSettingsUpdateRequest,
    current_user: CurrentUserDep,
    service: PortfolioServiceDep,
) -> PortfolioSettingsResponse:
    return _to_response(await service.update_settings(current_user, payload))


@router.post("/publish", response_model=PortfolioSettingsResponse)
async def publish_portfolio(
    current_user: CurrentUserDep, service: PortfolioServiceDep
) -> PortfolioSettingsResponse:
    return _to_response(await service.publish(current_user))


@router.post("/unpublish", response_model=PortfolioSettingsResponse)
async def unpublish_portfolio(
    current_user: CurrentUserDep, service: PortfolioServiceDep
) -> PortfolioSettingsResponse:
    return _to_response(await service.unpublish(current_user))


def _alias_response(
    link_slug: str, link_id: uuid.UUID, is_active: bool, in_plan: bool
) -> AliasResponse:
    return AliasResponse(
        id=link_id,
        slug=link_slug,
        subdomain=subdomain_for(link_slug),
        is_active=is_active,
        # False once the owner's plan no longer covers this alias: it stops resolving publicly
        # but is kept, and works again on re-upgrade.
        in_plan=in_plan,
    )


@router.get("/aliases", response_model=list[AliasResponse])
async def list_aliases(
    current_user: CurrentUserDep, service: PortfolioServiceDep
) -> list[AliasResponse]:
    return [
        _alias_response(link.slug, link.id, link.is_active, in_plan)
        for link, in_plan in await service.list_aliases(current_user)
    ]


@router.post("/aliases", response_model=AliasResponse, status_code=status.HTTP_201_CREATED)
async def add_alias(
    payload: AliasCreateRequest, current_user: CurrentUserDep, service: PortfolioServiceDep
) -> AliasResponse:
    link = await service.add_alias(current_user, payload.slug)
    return _alias_response(link.slug, link.id, link.is_active, True)


@router.patch("/aliases/{alias_id}", response_model=AliasResponse)
async def update_alias(
    alias_id: uuid.UUID,
    payload: AliasUpdateRequest,
    current_user: CurrentUserDep,
    service: PortfolioServiceDep,
) -> AliasResponse:
    await service.update_alias(
        current_user, alias_id, slug=payload.slug, is_active=payload.is_active
    )
    link, in_plan = next(
        (link, in_plan)
        for link, in_plan in await service.list_aliases(current_user)
        if link.id == alias_id
    )
    return _alias_response(link.slug, link.id, link.is_active, in_plan)


@router.delete("/aliases/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alias(
    alias_id: uuid.UUID, current_user: CurrentUserDep, service: PortfolioServiceDep
) -> None:
    await service.delete_alias(current_user, alias_id)
