from fastapi import APIRouter, status

from chatfolio.api.deps import CurrentUserDep, DbSessionDep, SettingsDep
from chatfolio.core.exceptions import NotFoundError
from chatfolio.repositories.profile_repository import ProfileRepository
from chatfolio.schemas.domain import AddDomainRequest, DomainResponse
from chatfolio.services.domain_service import DomainService
from chatfolio.services.portfolio_service import PortfolioService
from chatfolio.services.profile_service import ProfileService

router = APIRouter(prefix="/portfolio-settings/domain", tags=["custom-domain"])


def _service(session: DbSessionDep, settings: SettingsDep) -> DomainService:
    portfolio_service = PortfolioService(session, ProfileService(ProfileRepository(session)))
    return DomainService(session, portfolio_service, settings.features)


@router.get("", response_model=DomainResponse)
async def get_domain(
    current_user: CurrentUserDep, session: DbSessionDep, settings: SettingsDep
) -> DomainResponse:
    domain = await _service(session, settings).get_domain(current_user)
    if domain is None:
        raise NotFoundError("No custom domain is configured.")
    return DomainResponse.model_validate(domain)


@router.post("", response_model=DomainResponse, status_code=status.HTTP_201_CREATED)
async def add_domain(
    payload: AddDomainRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: SettingsDep,
) -> DomainResponse:
    domain = await _service(session, settings).add_domain(current_user, payload.domain)
    return DomainResponse.model_validate(domain)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def remove_domain(
    current_user: CurrentUserDep, session: DbSessionDep, settings: SettingsDep
) -> None:
    await _service(session, settings).remove_domain(current_user)
