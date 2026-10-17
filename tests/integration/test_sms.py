import pytest
from httpx import ASGITransport, AsyncClient

from chatfolio.main import app
from chatfolio.services.sms_service import SmsService, SmsTemplate, normalize_bd_mobile
from tests.conftest import fake_sms_sender
from tests.factories.fake_notifications import FakeSmsSender


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("01939504445", "8801939504445"),
        ("01812345678", "8801812345678"),
        ("01312345678", "8801312345678"),
        ("+8801512345678", "8801512345678"),
        ("8801412345678", "8801412345678"),
        ("019-3950 4445", "8801939504445"),
        ("0193950444", None),  # 10 digits
        ("019395044456", None),  # 12 digits
        ("01039504445", None),  # 010 is not a mobile operator prefix
        ("02123456789", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_bd_mobile(raw, expected) -> None:
    assert normalize_bd_mobile(raw) == expected


def test_templates_render_with_placeholders() -> None:
    assert "Rafi" in SmsService.render(SmsTemplate.WELCOME, name="Rafi")
    otp = SmsService.render(SmsTemplate.OTP_VERIFICATION, otp="123456", otp_ttl_minutes=10)
    assert "123456" in otp and "10 minutes" in otp
    alert = SmsService.render(
        SmsTemplate.RECRUITER_ALERT, name="A", recruiter_name="Sara", company="Acme"
    )
    assert "Sara" in alert and "Acme" in alert
    meeting = SmsService.render(
        SmsTemplate.MEETING_ALERT,
        name="A",
        recruiter_name="Sara",
        meeting_date="12 Oct 2026",
        meeting_time="05:00 PM",
    )
    assert "12 Oct 2026" in meeting and "05:00 PM" in meeting


async def test_register_with_valid_bd_phone_sends_welcome_sms() -> None:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "sms-welcome@example.com",
            "password": "password123",
            "phone": "01939504445",
            "full_name": "Rafi",
        },
    )
    assert response.status_code == 201
    assert len(fake_sms_sender.sent) == 1
    sent = fake_sms_sender.sent[0]
    assert sent["to"] == "8801939504445"
    assert "Rafi" in sent["message"]


async def test_register_with_invalid_phone_skips_sms() -> None:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "sms-invalid@example.com", "password": "password123", "phone": "12345"},
    )
    assert response.status_code == 201
    assert fake_sms_sender.sent == []


async def test_register_without_phone_skips_sms() -> None:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "sms-none@example.com", "password": "password123"},
    )
    assert response.status_code == 201
    assert fake_sms_sender.sent == []


async def test_gateway_failure_does_not_raise() -> None:
    class Boom(FakeSmsSender):
        async def send(self, **kwargs) -> None:
            raise RuntimeError("gateway down")

    await SmsService(Boom())._deliver(SmsTemplate.WELCOME, "8801939504445", None, {"name": "A"})
