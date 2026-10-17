import uuid

from fastapi import APIRouter, status

from chatfolio.api.deps import (
    CurrentUserDep,
    DbSessionDep,
    GuardDep,
    PortfolioServiceDep,
    SettingsDep,
)
from chatfolio.core.exceptions import NotFoundError
from chatfolio.schemas.domain import AddDomainRequest, DomainResponse
from chatfolio.services.domain_service import DomainService

router = APIRouter(prefix="/portfolio-settings/domain", tags=["custom-domain"])
# Several domains per chatfolio (up to the plan's `custom_domains_max`). The singular router
# above keeps the original one-domain contract, acting on the oldest domain.
domains_router = APIRouter(prefix="/portfolio-settings/domains", tags=["custom-domain"])


def _service(
    session: DbSessionDep,
    settings: SettingsDep,
    portfolio_service: PortfolioServiceDep,
    guard: GuardDep,
) -> DomainService:
    return DomainService(session, portfolio_service, settings.features, guard)


@router.get("", response_model=DomainResponse)
async def get_domain(
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: SettingsDep,
    portfolio_service: PortfolioServiceDep,
    guard: GuardDep,
) -> DomainResponse:
    service = _service(session, settings, portfolio_service, guard)
    domain = await service.get_domain(current_user)
    if domain is None:
        raise NotFoundError("No custom domain is configured.")
    return DomainResponse.model_validate(domain)


@router.post("", response_model=DomainResponse, status_code=status.HTTP_201_CREATED)
async def set_domain(
    payload: AddDomainRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: SettingsDep,
    portfolio_service: PortfolioServiceDep,
    guard: GuardDep,
) -> DomainResponse:
    service = _service(session, settings, portfolio_service, guard)
    return DomainResponse.model_validate(await service.replace_domain(current_user, payload.domain))


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def remove_domain(
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: SettingsDep,
    portfolio_service: PortfolioServiceDep,
    guard: GuardDep,
) -> None:
    await _service(session, settings, portfolio_service, guard).remove_domain(current_user)


@domains_router.get("", response_model=list[DomainResponse])
async def list_domains(
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: SettingsDep,
    portfolio_service: PortfolioServiceDep,
    guard: GuardDep,
) -> list[DomainResponse]:
    service = _service(session, settings, portfolio_service, guard)
    return [DomainResponse.model_validate(d) for d in await service.list_domains(current_user)]


@domains_router.post("", response_model=DomainResponse, status_code=status.HTTP_201_CREATED)
async def add_domain(
    payload: AddDomainRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: SettingsDep,
    portfolio_service: PortfolioServiceDep,
    guard: GuardDep,
) -> DomainResponse:
    service = _service(session, settings, portfolio_service, guard)
    return DomainResponse.model_validate(await service.add_domain(current_user, payload.domain))


@domains_router.delete("/{domain_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_domain_by_id(
    domain_id: uuid.UUID,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: SettingsDep,
    portfolio_service: PortfolioServiceDep,
    guard: GuardDep,
) -> None:
    await _service(session, settings, portfolio_service, guard).remove_domain(
        current_user, domain_id
    )
