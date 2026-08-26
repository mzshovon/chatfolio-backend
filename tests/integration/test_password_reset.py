import re

from tests.conftest import fake_email_sender
from tests.factories.publish_flow import authed_client


def _extract_reset_token(body: str) -> str:
    match = re.search(r"token=([\w-]+)", body)
    assert match is not None, body
    return match.group(1)


async def test_forgot_password_then_reset_allows_login_with_new_password() -> None:
    email = "reset-happy@example.com"
    client, _headers = await authed_client(email)

    response = await client.post("/api/v1/auth/forgot-password", json={"email": email})
    assert response.status_code == 204
    assert len(fake_email_sender.sent) == 1
    assert fake_email_sender.sent[0]["to"] == email
    token = _extract_reset_token(fake_email_sender.sent[0]["body"])

    reset_response = await client.post(
        "/api/v1/auth/reset-password", json={"token": token, "new_password": "brandnewpass123"}
    )
    assert reset_response.status_code == 204

    old_login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "supersecret123"}
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "brandnewpass123"}
    )
    assert new_login.status_code == 200


async def test_forgot_password_for_unknown_email_returns_204_without_sending() -> None:
    client, _headers = await authed_client("someone-else@example.com")

    response = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "never-registered@example.com"}
    )
    assert response.status_code == 204
    assert fake_email_sender.sent == []


async def test_reset_password_rejects_invalid_token() -> None:
    client, _headers = await authed_client("reset-invalid-token@example.com")

    response = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": "not-a-real-token", "new_password": "whatever123"},
    )
    assert response.status_code == 401


async def test_reset_password_token_is_single_use() -> None:
    email = "reset-single-use@example.com"
    client, _headers = await authed_client(email)

    await client.post("/api/v1/auth/forgot-password", json={"email": email})
    token = _extract_reset_token(fake_email_sender.sent[0]["body"])

    first = await client.post(
        "/api/v1/auth/reset-password", json={"token": token, "new_password": "firstnewpass123"}
    )
    assert first.status_code == 204

    second = await client.post(
        "/api/v1/auth/reset-password", json={"token": token, "new_password": "secondnewpass123"}
    )
    assert second.status_code == 401


async def test_reset_password_revokes_existing_refresh_tokens() -> None:
    email = "reset-revokes-sessions@example.com"
    client, _headers = await authed_client(email)
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "supersecret123"}
    )
    refresh_token = login.json()["refresh_token"]

    await client.post("/api/v1/auth/forgot-password", json={"email": email})
    token = _extract_reset_token(fake_email_sender.sent[0]["body"])
    await client.post(
        "/api/v1/auth/reset-password", json={"token": token, "new_password": "brandnewpass123"}
    )

    refresh_response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert refresh_response.status_code == 401
