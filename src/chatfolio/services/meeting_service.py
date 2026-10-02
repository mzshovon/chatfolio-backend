import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt as pyjwt
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.config.settings import SecuritySettings
from chatfolio.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    TooManyRequestsError,
    UnauthorizedError,
    ValidationFailedError,
)
from chatfolio.core.security import (
    create_google_oauth_state_token,
    decode_google_oauth_state_token,
    decrypt_secret,
    encrypt_secret,
)
from chatfolio.models.chat import ChatSession
from chatfolio.models.chatfolio import PublicChatfolio
from chatfolio.models.meeting import GoogleCalendarConnection
from chatfolio.models.profile import CandidateProfile
from chatfolio.models.user import User
from chatfolio.repositories.meeting_repository import MeetingRepository
from chatfolio.schemas.meeting import MeetingRequest, MeetingResponse
from chatfolio.services.google_oauth_client import GoogleOAuthClient


class MeetingSettingsService:
    # Refresh a bit before actual expiry so we don't hand a token to a caller that dies mid-request.
    _EXPIRY_BUFFER = timedelta(minutes=5)
    # Minimum gap between public meeting requests to the same candidate, across all sessions.
    _PUBLIC_REQUEST_COOLDOWN = timedelta(minutes=5)

    def __init__(
        self,
        session: AsyncSession,
        repository: MeetingRepository,
        oauth_client: GoogleOAuthClient,
        security_settings: SecuritySettings,
    ) -> None:
        self._session = session
        self._repository = repository
        self._oauth_client = oauth_client
        self._security_settings = security_settings

    async def get_connection(self, user: User) -> GoogleCalendarConnection | None:
        return await self._repository.get_by_user_id(user.id)

    def build_connect_url(self, user: User) -> str:
        state = create_google_oauth_state_token(user_id=user.id, settings=self._security_settings)
        return self._oauth_client.build_authorization_url(state=state)

    async def handle_callback(self, *, state: str, code: str) -> uuid.UUID:
        """Exchanges the authorization code and upserts the connection. Returns the user_id the
        state token identified, so the router can redirect back to that user's settings page."""
        try:
            user_id = decode_google_oauth_state_token(state, self._security_settings)
        except pyjwt.InvalidTokenError as exc:
            raise UnauthorizedError("Google OAuth state is invalid or has expired.") from exc

        tokens = await self._oauth_client.exchange_code(code=code)
        access_token = tokens["access_token"]
        refresh_token = tokens.get("refresh_token")
        expires_at = datetime.now(UTC) + timedelta(seconds=tokens.get("expires_in", 3600))
        scope = tokens.get("scope", "")

        userinfo = await self._oauth_client.fetch_userinfo(access_token=access_token)
        account_email = userinfo.get("email")

        connection = await self._repository.get_by_user_id(user_id)
        if connection is None:
            connection = GoogleCalendarConnection(
                user_id=user_id,
                access_token_encrypted=encrypt_secret(access_token, self._security_settings),
                refresh_token_encrypted=(
                    encrypt_secret(refresh_token, self._security_settings)
                    if refresh_token
                    else None
                ),
                access_token_expires_at=expires_at,
                granted_scopes=scope,
                google_account_email=account_email,
            )
            await self._repository.create(connection)
        else:
            connection.access_token_encrypted = encrypt_secret(
                access_token, self._security_settings
            )
            # Google only re-issues a refresh_token on some consent screens — keep the existing
            # one on a reconnect that didn't get a fresh one rather than nulling out access.
            if refresh_token:
                connection.refresh_token_encrypted = encrypt_secret(
                    refresh_token, self._security_settings
                )
            connection.access_token_expires_at = expires_at
            connection.granted_scopes = scope
            connection.google_account_email = account_email or connection.google_account_email
            await self._repository.save(connection)

        return user_id

    async def get_valid_access_token(self, user: User) -> str:
        return await self._valid_access_token_for(user.id)

    async def _valid_access_token_for(self, user_id: uuid.UUID) -> str:
        """Returns a Google access token usable right now against the Calendar API, refreshing
        it first if it's expired (or about to be). This is what any Calendar-calling code
        (event creation, availability checks, etc.) should call instead of reading
        access_token_encrypted off the connection directly."""
        connection = await self._repository.get_by_user_id(user_id)
        if connection is None:
            raise NotFoundError("Google Calendar is not connected.")

        if connection.access_token_expires_at > datetime.now(UTC) + self._EXPIRY_BUFFER:
            return decrypt_secret(connection.access_token_encrypted, self._security_settings)

        if not connection.refresh_token_encrypted:
            raise UnauthorizedError(
                "Google Calendar access has expired and must be reconnected."
            )

        refresh_token = decrypt_secret(
            connection.refresh_token_encrypted, self._security_settings
        )
        tokens = await self._oauth_client.refresh_access_token(refresh_token=refresh_token)
        access_token = tokens["access_token"]
        expires_at = datetime.now(UTC) + timedelta(seconds=tokens.get("expires_in", 3600))

        connection.access_token_encrypted = encrypt_secret(access_token, self._security_settings)
        connection.access_token_expires_at = expires_at
        await self._repository.save(connection)

        return access_token

    async def disconnect(self, user: User) -> None:
        connection = await self._repository.get_by_user_id(user.id)
        if connection is None:
            return
        if connection.refresh_token_encrypted:
            token = decrypt_secret(connection.refresh_token_encrypted, self._security_settings)
            await self._oauth_client.revoke(token=token)
        else:
            token = decrypt_secret(connection.access_token_encrypted, self._security_settings)
            await self._oauth_client.revoke(token=token)
        await self._repository.delete(connection)

    async def schedule_meeting(self, user: User, request: MeetingRequest) -> MeetingResponse:
        """Creates a Google Calendar event with a Meet link on the candidate's primary calendar
        and emails the invite to the attendee."""
        return await self._schedule_meeting_for(user.id, request)

    async def schedule_public_meeting(
        self,
        session_id: uuid.UUID,
        request: MeetingRequest,
        expected_user_id: uuid.UUID | None = None,
    ) -> MeetingResponse:
        """Recruiter-initiated variant, called from the public chat: the candidate is resolved
        from the chat session (never from client input), and every connection problem collapses
        into one generic 409 so a recruiter learns nothing about the candidate's account state
        (not connected vs. token revoked) and never sees a misleading 401/404."""
        chat_session = await self._session.get(ChatSession, session_id)
        if chat_session is None:
            raise NotFoundError("Chat session not found.")
        chatfolio = await self._session.get(PublicChatfolio, chat_session.chatfolio_id)
        if chatfolio is None or not chatfolio.is_published:
            raise NotFoundError("This Chatfolio is not available.")
        profile = await self._session.get(CandidateProfile, chatfolio.profile_id)
        if profile is None or (expected_user_id and expected_user_id != profile.user_id):
            raise NotFoundError("This Chatfolio is not available.")

        # Lock first so the checks below and the insert are atomic per candidate.
        await self._repository.lock_candidate_requests(profile.user_id)
        if await self._repository.session_has_request(session_id):
            raise ForbiddenError("A meeting has already been requested from this chat.")
        latest = await self._repository.latest_request_at(profile.user_id)
        if latest is not None and datetime.now(UTC) - latest < self._PUBLIC_REQUEST_COOLDOWN:
            raise TooManyRequestsError(
                "A meeting was just requested with this candidate. Please try again in a few minutes."
            )

        try:
            meeting = await self._schedule_meeting_for(profile.user_id, request)
        except (NotFoundError, UnauthorizedError, ValidationFailedError) as exc:
            # ValidationFailedError here covers Google rejecting the stored refresh token (revoked
            # access) as well as a bad `start`; only the former is a connection problem.
            if isinstance(exc, ValidationFailedError) and "refresh token" not in exc.message:
                raise
            raise ConflictError(
                "This candidate isn't accepting meeting requests right now."
            ) from exc

        # Only recorded once Google accepted the event: a failed attempt must not burn the
        # session's one request or start the candidate's cool-down.
        await self._repository.record_request(session_id, profile.user_id)
        return meeting

    async def _schedule_meeting_for(
        self, user_id: uuid.UUID, request: MeetingRequest
    ) -> MeetingResponse:
        if request.start <= datetime.now(UTC):
            raise ValidationFailedError("Meeting start must be in the future.")

        access_token = await self._valid_access_token_for(user_id)
        end = request.start + timedelta(minutes=request.duration_minutes)
        # Main attendee first; additional_attendees is already validated/deduplicated by the
        # schema, so only the overlap with the main attendee is left to drop here.
        attendee_emails = [request.attendee_email]
        for extra in (request.additional_attendees or "").split(","):
            if extra and extra.lower() not in {e.lower() for e in attendee_emails}:
                attendee_emails.append(extra)
        event: dict[str, Any] = {
            "summary": request.title,
            "description": request.description,
            "start": {"dateTime": request.start.isoformat(), "timeZone": request.timezone},
            "end": {"dateTime": end.isoformat(), "timeZone": request.timezone},
            "attendees": [{"email": email} for email in attendee_emails],
            "conferenceData": {
                "createRequest": {
                    "requestId": request.request_id or f"chatfolio-{uuid.uuid4()}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }
        created = await self._oauth_client.create_calendar_event(
            access_token=access_token, event=event
        )

        meet_link = created.get("hangoutLink")
        if meet_link is None:
            for entry_point in created.get("conferenceData", {}).get("entryPoints", []):
                if entry_point.get("entryPointType") == "video":
                    meet_link = entry_point.get("uri")
                    break

        return MeetingResponse(
            event_id=created["id"],
            status=created.get("status", "confirmed"),
            meet_link=meet_link,
            calendar_link=created.get("htmlLink"),
            title=request.title,
            start=request.start,
            end=end,
            timezone=request.timezone,
            attendee_email=request.attendee_email,
            attendee_emails=attendee_emails,
        )
