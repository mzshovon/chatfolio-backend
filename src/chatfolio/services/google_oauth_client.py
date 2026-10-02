from typing import Any
from urllib.parse import urlencode

import httpx

from chatfolio.config.settings import GoogleOAuthSettings
from chatfolio.core.exceptions import (
    ServiceUnavailableError,
    UnauthorizedError,
    ValidationFailedError,
)


class GoogleOAuthClient:
    """Thin wrapper around the handful of Google OAuth2/Calendar REST calls the meeting-settings
    flow needs. No SDK dependency — it's four plain REST calls, not worth pulling in
    google-auth/google-api-python-client for."""

    def __init__(self, settings: GoogleOAuthSettings) -> None:
        self._settings = settings

    def build_authorization_url(self, *, state: str) -> str:
        if not self._settings.client_id:
            raise ServiceUnavailableError("Google Calendar integration is not configured.")
        params = {
            "client_id": self._settings.client_id,
            "redirect_uri": self._settings.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self._settings.scopes),
            # offline + consent: without both, Google only issues a refresh_token on a user's
            # very first-ever consent for this client, which breaks reconnect-after-revoke.
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{self._settings.authorize_url}?{urlencode(params)}"

    async def exchange_code(self, *, code: str) -> dict[str, Any]:
        if not self._settings.client_id or not self._settings.client_secret:
            raise ServiceUnavailableError("Google Calendar integration is not configured.")
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self._settings.token_url,
                data={
                    "code": code,
                    "client_id": self._settings.client_id,
                    "client_secret": self._settings.client_secret.get_secret_value(),
                    "redirect_uri": self._settings.redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
        if response.status_code >= 400:
            # Most commonly an expired/already-used `code` (they're single-use, short-lived) —
            # a 4xx from us, not a 5xx, since it's a bad request from the caller's flow, not an
            # outage on our end.
            raise ValidationFailedError("Google rejected the authorization code.")
        return response.json()

    async def fetch_userinfo(self, *, access_token: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                self._settings.userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if response.status_code >= 400:
            return {}
        return response.json()

    async def refresh_access_token(self, *, refresh_token: str) -> dict[str, Any]:
        if not self._settings.client_id or not self._settings.client_secret:
            raise ServiceUnavailableError("Google Calendar integration is not configured.")
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self._settings.token_url,
                data={
                    "refresh_token": refresh_token,
                    "client_id": self._settings.client_id,
                    "client_secret": self._settings.client_secret.get_secret_value(),
                    "grant_type": "refresh_token",
                },
            )
        if response.status_code >= 400:
            # Refresh tokens can be revoked/expired independently of the connection row (user
            # revoked access from their Google account, password change, etc.) — treat that as
            # "the connection needs to be re-established", not a transient failure.
            raise ValidationFailedError("Google rejected the refresh token.")
        return response.json()

    async def revoke(self, *, token: str) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Best-effort: Google returns 200 even for an already-invalid token, and a revoke
            # failure shouldn't block deleting our own local copy of the connection.
            await client.post(self._settings.revoke_url, data={"token": token})

    async def create_calendar_event(
        self, *, access_token: str, event: dict[str, Any]
    ) -> dict[str, Any]:
        """Inserts an event on the connected account's primary calendar. `conferenceDataVersion=1`
        is what makes Google honour the `conferenceData.createRequest` (Meet link) in the body, and
        `sendUpdates=all` makes Google email the invite to every attendee."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                self._settings.calendar_events_url,
                params={"conferenceDataVersion": 1, "sendUpdates": "all"},
                headers={"Authorization": f"Bearer {access_token}"},
                json=event,
            )
        if response.status_code in (401, 403):
            # Token revoked or the Calendar scope wasn't granted — only a reconnect fixes it.
            raise UnauthorizedError(
                "Google Calendar access was denied and must be reconnected."
            )
        if response.status_code >= 500:
            raise ServiceUnavailableError("Google Calendar is temporarily unavailable.")
        if response.status_code >= 400:
            raise ValidationFailedError("Google rejected the meeting request.")
        return response.json()
