from fastapi import APIRouter

from chatfolio.api.deps import CurrentUserDep, DbSessionDep
from chatfolio.models.chatfolio import PublicChatfolio
from chatfolio.repositories.profile_repository import ProfileRepository
from chatfolio.schemas.portfolio_settings import (
    PortfolioSettingsResponse,
    PortfolioSettingsUpdateRequest,
)
from chatfolio.services.portfolio_service import PortfolioService, subdomain_for
from chatfolio.services.profile_service import ProfileService

router = APIRouter(prefix="/portfolio-settings", tags=["portfolio-settings"])


def _service(session: DbSessionDep) -> PortfolioService:
    return PortfolioService(session, ProfileService(ProfileRepository(session)))


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
    current_user: CurrentUserDep, session: DbSessionDep
) -> PortfolioSettingsResponse:
    chatfolio = await _service(session).get_or_create_for_user(current_user)
    return _to_response(chatfolio)


@router.patch("", response_model=PortfolioSettingsResponse)
async def update_portfolio_settings(
    payload: PortfolioSettingsUpdateRequest, current_user: CurrentUserDep, session: DbSessionDep
) -> PortfolioSettingsResponse:
    chatfolio = await _service(session).update_settings(current_user, payload)
    return _to_response(chatfolio)


@router.post("/publish", response_model=PortfolioSettingsResponse)
async def publish_portfolio(
    current_user: CurrentUserDep, session: DbSessionDep
) -> PortfolioSettingsResponse:
    chatfolio = await _service(session).publish(current_user)
    return _to_response(chatfolio)


@router.post("/unpublish", response_model=PortfolioSettingsResponse)
async def unpublish_portfolio(
    current_user: CurrentUserDep, session: DbSessionDep
) -> PortfolioSettingsResponse:
    chatfolio = await _service(session).unpublish(current_user)
    return _to_response(chatfolio)
