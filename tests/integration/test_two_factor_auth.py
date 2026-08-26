import re

from tests.conftest import fake_email_sender, fake_sms_sender
from tests.factories.publish_flow import authed_client


def _extract_code(body: str) -> str:
    match = re.search(r"code is (\d{6})", body)
    assert match is not None, body
    return match.group(1)


async def _enable_email_two_factor(client, headers) -> None:
    setup = await client.post("/api/v1/auth/2fa/setup", headers=headers, json={"method": "email"})
    assert setup.status_code == 200
    code = _extract_code(fake_email_sender.sent[-1]["body"])
    verify = await client.post(
        "/api/v1/auth/2fa/verify-setup", headers=headers, json={"code": code}
    )
    assert verify.status_code == 204


async def test_setup_two_factor_email_masks_destination() -> None:
    email = "twofactor-setup@example.com"
    client, headers = await authed_client(email)

    setup = await client.post("/api/v1/auth/2fa/setup", headers=headers, json={"method": "email"})
    assert setup.status_code == 200
    body = setup.json()
    assert body["method"] == "email"
    assert body["masked_destinations"] == [f"{email[0]}•••@{email.split('@')[1]}"]


async def test_two_factor_setup_verify_rejects_wrong_code() -> None:
    email = "twofactor-wrong-code@example.com"
    client, headers = await authed_client(email)

    await client.post("/api/v1/auth/2fa/setup", headers=headers, json={"method": "email"})
    response = await client.post(
        "/api/v1/auth/2fa/verify-setup", headers=headers, json={"code": "000000"}
    )
    assert response.status_code == 401


async def test_phone_method_requires_phone_number() -> None:
    email = "twofactor-needs-phone@example.com"
    client, headers = await authed_client(email)

    response = await client.post(
        "/api/v1/auth/2fa/setup", headers=headers, json={"method": "phone"}
    )
    assert response.status_code == 422


async def test_login_with_two_factor_enabled_requires_otp_step() -> None:
    email = "twofactor-login@example.com"
    client, headers = await authed_client(email)
    await _enable_email_two_factor(client, headers)

    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "supersecret123"}
    )
    assert login.status_code == 200
    body = login.json()
    assert body["requires_two_factor"] is True
    assert "access_token" not in body
    challenge_token = body["challenge_token"]

    code = _extract_code(fake_email_sender.sent[-1]["body"])
    verify = await client.post(
        "/api/v1/auth/2fa/login/verify", json={"challenge_token": challenge_token, "code": code}
    )
    assert verify.status_code == 200
    assert "access_token" in verify.json()


async def test_login_two_factor_verify_rejects_wrong_code() -> None:
    email = "twofactor-login-wrong@example.com"
    client, headers = await authed_client(email)
    await _enable_email_two_factor(client, headers)

    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "supersecret123"}
    )
    challenge_token = login.json()["challenge_token"]

    verify = await client.post(
        "/api/v1/auth/2fa/login/verify", json={"challenge_token": challenge_token, "code": "000000"}
    )
    assert verify.status_code == 401


async def test_resend_two_factor_code_invalidates_previous_code() -> None:
    email = "twofactor-resend@example.com"
    client, headers = await authed_client(email)
    await _enable_email_two_factor(client, headers)

    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "supersecret123"}
    )
    challenge_token = login.json()["challenge_token"]
    old_code = _extract_code(fake_email_sender.sent[-1]["body"])

    resend = await client.post(
        "/api/v1/auth/2fa/login/resend", json={"challenge_token": challenge_token}
    )
    assert resend.status_code == 204
    new_code = _extract_code(fake_email_sender.sent[-1]["body"])
    assert new_code != old_code

    stale = await client.post(
        "/api/v1/auth/2fa/login/verify", json={"challenge_token": challenge_token, "code": old_code}
    )
    assert stale.status_code == 401

    fresh = await client.post(
        "/api/v1/auth/2fa/login/verify", json={"challenge_token": challenge_token, "code": new_code}
    )
    assert fresh.status_code == 200


async def test_two_factor_both_method_sends_email_and_sms() -> None:
    email = "twofactor-both@example.com"
    client, headers = await authed_client(email)

    setup = await client.post(
        "/api/v1/auth/2fa/setup",
        headers=headers,
        json={"method": "both", "phone": "+15551234567"},
    )
    assert setup.status_code == 200
    assert len(setup.json()["masked_destinations"]) == 2
    assert fake_email_sender.sent
    assert fake_sms_sender.sent
    assert fake_sms_sender.sent[-1]["to"] == "+15551234567"

    email_code = _extract_code(fake_email_sender.sent[-1]["body"])
    verify = await client.post(
        "/api/v1/auth/2fa/verify-setup", headers=headers, json={"code": email_code}
    )
    assert verify.status_code == 204
