import uuid
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, EmailStr, Field, field_validator


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


class MeetingRequest(BaseModel):
    """Everything the frontend sends to schedule a Google Meet. Deliberately small: the backend
    builds the Google event payload (end time, requestId, conference config) itself."""

    attendee_email: EmailStr
    # Must carry a UTC offset (e.g. "2026-09-19T17:00:00+06:00" or "...Z") — a naive datetime
    # would be silently interpreted in the server's timezone.
    start: datetime
    duration_minutes: int = Field(default=30, ge=5, le=480)
    # IANA name, e.g. "Asia/Dhaka". Only affects how Google displays the event.
    timezone: str = "UTC"
    title: str = Field(default="Interview via Chatfolio", min_length=1, max_length=255)
    description: str | None = Field(default="Interview scheduled through Chatfolio.", max_length=2000)
    # Optional client-generated idempotency key: resending the same value (e.g. a double click or
    # a retry after a timeout) reuses Google's conference request instead of creating a second
    # Meet. Generated server-side when omitted.
    request_id: str | None = Field(default=None, min_length=8, max_length=100)

    @field_validator("start")
    @classmethod
    def _start_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("start must include a UTC offset, e.g. 2026-09-19T17:00:00+06:00")
        return value

    @field_validator("timezone")
    @classmethod
    def _timezone_must_be_iana(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be an IANA name, e.g. Asia/Dhaka") from exc
        return value


class MeetingResponse(BaseModel):
    event_id: str
    status: str
    # What the UI shows/copies. Can be null if Google is still provisioning the conference
    # (rare) — the invite email still carries it once ready.
    meet_link: str | None
    # Opens the event in Google Calendar.
    calendar_link: str | None
    title: str
    start: datetime
    end: datetime
    timezone: str
    attendee_email: str


class PublicMeetingRequest(BaseModel):
    """What a recruiter sends from the public chat. A subset of MeetingRequest: the recruiter
    can't choose the event title, and the candidate is identified by the chat session in the
    URL, never by anything in this body."""

    # Optional guard: the candidate's user id, as known to the caller. Checked against the
    # candidate the chat session belongs to — a mismatch is rejected rather than silently
    # booking someone else's calendar. Never used to *pick* the candidate (the session does).
    user_id: uuid.UUID | None = None
    attendee_email: EmailStr
    attendee_name: str | None = Field(default=None, max_length=100)
    start: datetime
    duration_minutes: int = Field(default=30, ge=15, le=120)
    timezone: str = "UTC"
    message: str | None = Field(default=None, max_length=500)
    request_id: str | None = Field(default=None, min_length=8, max_length=100)

    _tz_aware = field_validator("start")(MeetingRequest._start_must_be_timezone_aware.__func__)
    _iana = field_validator("timezone")(MeetingRequest._timezone_must_be_iana.__func__)


class PublicMeetingResponse(BaseModel):
    # Deliberately omits event_id/calendar_link (the candidate's own calendar) — the recruiter
    # gets the Meet link and the confirmed time, plus the invite email Google sends them.
    meet_link: str | None
    title: str
    start: datetime
    end: datetime
    timezone: str
