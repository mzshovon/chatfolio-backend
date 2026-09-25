from datetime import datetime

from pydantic import BaseModel


class GoogleConnectResponse(BaseModel):
    # Frontend does a top-level navigation (window.location.href = ...) to this URL rather than
    # calling it via fetch/XHR — the consent screen can't be shown inside an API response.
    authorization_url: str


class MeetingSettingsResponse(BaseModel):
    google_connected: bool
    google_account_email: str | None
    granted_scopes: list[str]
    access_token_expires_at: datetime | None
    # Refreshed on the fly if the stored one is expired/near-expiry — always usable at the
    # moment this response is generated. Only present when google_connected is true.
    access_token: str | None = None
