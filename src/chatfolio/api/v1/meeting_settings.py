import structlog
from fastapi import APIRouter, Query, Request, status
from fastapi.responses import RedirectResponse

from chatfolio.api.deps import CurrentUserDep, DbSessionDep, SettingsDep
from chatfolio.core.exceptions import ChatfolioError
from chatfolio.core.rate_limit import limiter
from chatfolio.models.meeting import GoogleCalendarConnection
from chatfolio.repositories.meeting_repository import MeetingRepository
from chatfolio.schemas.meeting import (
    GoogleConnectResponse,
    MeetingRequest,
    MeetingResponse,
    MeetingSettingsResponse,
)
from chatfolio.services.google_oauth_client import GoogleOAuthClient
from chatfolio.services.meeting_service import MeetingSettingsService

router = APIRouter(prefix="/meeting-settings", tags=["meeting-settings"])
logger = structlog.get_logger()


def _service(session: DbSessionDep, settings: SettingsDep) -> MeetingSettingsService:
    return MeetingSettingsService(
        session,
        MeetingRepository(session),
        GoogleOAuthClient(settings.google_oauth),
        settings.security,
    )


def _to_response(
    connection: GoogleCalendarConnection | None, access_token: str | None = None
) -> MeetingSettingsResponse:
    if connection is None:
        return MeetingSettingsResponse(
            google_connected=False,
            google_account_email=None,
            granted_scopes=[],
            access_token_expires_at=None,
        )
    return MeetingSettingsResponse(
        google_connected=True,
        google_account_email=connection.google_account_email,
        granted_scopes=connection.granted_scopes.split() if connection.granted_scopes else [],
        access_token_expires_at=connection.access_token_expires_at,
        access_token=access_token,
    )


@router.get("", response_model=MeetingSettingsResponse)
async def get_meeting_settings(
    current_user: CurrentUserDep, session: DbSessionDep, settings: SettingsDep
) -> MeetingSettingsResponse:
    service = _service(session, settings)
    connection = await service.get_connection(current_user)
    access_token = (
        await service.get_valid_access_token(current_user) if connection is not None else None
    )
    return _to_response(connection, access_token)


@router.get("/google/connect", response_model=GoogleConnectResponse)
async def connect_google(
    current_user: CurrentUserDep, session: DbSessionDep, settings: SettingsDep
) -> GoogleConnectResponse:
    url = _service(session, settings).build_connect_url(current_user)
    return GoogleConnectResponse(authorization_url=url)


@router.get("/google/callback", include_in_schema=False)
@limiter.limit("20/minute")
async def google_callback(
    request: Request,
    session: DbSessionDep,
    settings: SettingsDep,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    # This is Google redirecting the candidate's browser directly, not an API call the frontend
    # makes — so the response is a 302 back into the app, never JSON. `state` (not a bearer
    # token) is what ties this request back to the user who started the flow; see
    # MeetingSettingsService.build_connect_url / handle_callback.
    settings_path = f"{settings.app.frontend_base_url}/dashboard/settings"

    if error or not code or not state:
        logger.warning("meeting_settings.google_callback_denied", error=error)
        return RedirectResponse(f"{settings_path}?meeting_settings=google_error")

    try:
        await _service(session, settings).handle_callback(state=state, code=code)
    except ChatfolioError:
        logger.warning("meeting_settings.google_callback_failed", exc_info=True)
        return RedirectResponse(f"{settings_path}?meeting_settings=google_error")

    return RedirectResponse(f"{settings_path}?meeting_settings=google_connected")


@router.delete("/google", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_google(
    current_user: CurrentUserDep, session: DbSessionDep, settings: SettingsDep
) -> None:
    await _service(session, settings).disconnect(current_user)


@router.post("/meetings", response_model=MeetingResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
async def schedule_meeting(
    request: Request,
    body: MeetingRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: SettingsDep,
) -> MeetingResponse:
    return await _service(session, settings).schedule_meeting(current_user, body)
