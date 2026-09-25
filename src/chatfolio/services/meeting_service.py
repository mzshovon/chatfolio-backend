import uuid
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.config.settings import SecuritySettings
from chatfolio.core.exceptions import NotFoundError, UnauthorizedError
from chatfolio.core.security import (
    create_google_oauth_state_token,
    decode_google_oauth_state_token,
    decrypt_secret,
    encrypt_secret,
)
from chatfolio.models.meeting import GoogleCalendarConnection
from chatfolio.models.user import User
from chatfolio.repositories.meeting_repository import MeetingRepository
from chatfolio.services.google_oauth_client import GoogleOAuthClient


class MeetingSettingsService:
    # Refresh a bit before actual expiry so we don't hand a token to a caller that dies mid-request.
    _EXPIRY_BUFFER = timedelta(minutes=5)

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
        """Returns a Google access token usable right now against the Calendar API, refreshing
        it first if it's expired (or about to be). This is what any Calendar-calling code
        (event creation, availability checks, etc.) should call instead of reading
        access_token_encrypted off the connection directly."""
        connection = await self._repository.get_by_user_id(user.id)
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
