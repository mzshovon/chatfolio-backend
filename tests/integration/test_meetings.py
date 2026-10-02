from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from chatfolio.config.settings import SecuritySettings
from chatfolio.core.security import encrypt_secret
from chatfolio.db.session import get_sessionmaker
from chatfolio.models.meeting import GoogleCalendarConnection
from chatfolio.models.user import User
from chatfolio.services.google_oauth_client import GoogleOAuthClient
from tests.factories.publish_flow import authed_client

URL = "/api/v1/meeting-settings/meetings"


def _future_start() -> str:
    return (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0).isoformat()


async def _connect_google(email: str) -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        session.add(
            GoogleCalendarConnection(
                user_id=user.id,
                access_token_encrypted=encrypt_secret("ya29.fake", SecuritySettings()),
                access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
                granted_scopes="https://www.googleapis.com/auth/calendar.events",
            )
        )
        await session.commit()


@pytest.fixture
def captured_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    async def fake_create(
        self: GoogleOAuthClient, *, access_token: str, event: dict[str, Any]
    ) -> dict[str, Any]:
        assert access_token == "ya29.fake"
        events.append(event)
        return {
            "id": "evt123",
            "status": "confirmed",
            "htmlLink": "https://calendar.google.com/event?eid=evt123",
            "hangoutLink": "https://meet.google.com/abc-defg-hij",
        }

    monkeypatch.setattr(GoogleOAuthClient, "create_calendar_event", fake_create)
    return events


async def test_schedule_meeting_requires_auth() -> None:
    client, _ = await authed_client("meet-anon@example.com")
    response = await client.post(URL, json={"attendee_email": "a@example.com", "start": _future_start()})
    assert response.status_code == 401


async def test_schedule_meeting_requires_connected_calendar(
    captured_events: list[dict[str, Any]],
) -> None:
    client, headers = await authed_client("meet-unconnected@example.com")
    response = await client.post(
        URL, headers=headers, json={"attendee_email": "a@example.com", "start": _future_start()}
    )
    assert response.status_code == 404
    assert captured_events == []


async def test_schedule_meeting_returns_meet_link(captured_events: list[dict[str, Any]]) -> None:
    email = "meet-ok@example.com"
    client, headers = await authed_client(email)
    await _connect_google(email)

    response = await client.post(
        URL,
        headers=headers,
        json={
            "attendee_email": "recruiter@example.com",
            "start": _future_start(),
            "duration_minutes": 45,
            "timezone": "Asia/Dhaka",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["meet_link"] == "https://meet.google.com/abc-defg-hij"
    assert body["event_id"] == "evt123"
    assert body["attendee_email"] == "recruiter@example.com"
    start = datetime.fromisoformat(body["start"])
    assert datetime.fromisoformat(body["end"]) - start == timedelta(minutes=45)

    sent = captured_events[0]
    assert sent["attendees"] == [{"email": "recruiter@example.com"}]
    assert sent["start"]["timeZone"] == "Asia/Dhaka"
    create_request = sent["conferenceData"]["createRequest"]
    assert create_request["conferenceSolutionKey"] == {"type": "hangoutsMeet"}
    assert create_request["requestId"].startswith("chatfolio-")


async def test_schedule_meeting_uses_client_request_id(
    captured_events: list[dict[str, Any]],
) -> None:
    email = "meet-idem@example.com"
    client, headers = await authed_client(email)
    await _connect_google(email)
    await client.post(
        URL,
        headers=headers,
        json={
            "attendee_email": "r@example.com",
            "start": _future_start(),
            "request_id": "my-booking-0001",
        },
    )
    assert captured_events[0]["conferenceData"]["createRequest"]["requestId"] == "my-booking-0001"


@pytest.mark.parametrize(
    "overrides",
    [
        {"start": "2026-01-01T10:00:00"},  # naive datetime
        {"start": "2020-01-01T10:00:00+00:00"},  # in the past
        {"timezone": "Mars/Phobos"},
        {"duration_minutes": 1},
        {"attendee_email": "not-an-email"},
    ],
)
async def test_schedule_meeting_validation(
    overrides: dict[str, Any], captured_events: list[dict[str, Any]]
) -> None:
    email = f"meet-invalid-{abs(hash(str(overrides)))}@example.com"
    client, headers = await authed_client(email)
    await _connect_google(email)
    payload = {"attendee_email": "r@example.com", "start": _future_start(), **overrides}
    response = await client.post(URL, headers=headers, json=payload)
    assert response.status_code == 422
    assert captured_events == []
