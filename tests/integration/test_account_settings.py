import re

from tests.conftest import fake_email_sender
from tests.factories.publish_flow import authed_client


def _extract_confirm_token(body: str) -> str:
    match = re.search(r"token=([\w-]+)", body)
    assert match is not None, body
    return match.group(1)


async def test_change_password_then_login_with_new_password() -> None:
    email = "changepass-happy@example.com"
    client, headers = await authed_client(email)

    response = await client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": "supersecret123", "new_password": "brandnewpass456"},
    )
    assert response.status_code == 204

    old_login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "supersecret123"}
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "brandnewpass456"}
    )
    assert new_login.status_code == 200


async def test_change_password_rejects_wrong_current_password() -> None:
    client, headers = await authed_client("changepass-wrong@example.com")

    response = await client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": "not-the-real-password", "new_password": "brandnewpass456"},
    )
    assert response.status_code == 401


async def test_change_password_does_not_revoke_other_sessions() -> None:
    email = "changepass-keeps-sessions@example.com"
    client, headers = await authed_client(email)
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "supersecret123"}
    )
    refresh_token = login.json()["refresh_token"]

    await client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": "supersecret123", "new_password": "brandnewpass456"},
    )

    refresh_response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert refresh_response.status_code == 200


async def test_change_password_requires_auth() -> None:
    client, _ = await authed_client("changepass-noauth@example.com")
    response = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "supersecret123", "new_password": "brandnewpass456"},
    )
    assert response.status_code == 401


async def test_request_and_confirm_email_change() -> None:
    email = "changeemail-happy@example.com"
    new_email = "changeemail-happy-new@example.com"
    client, headers = await authed_client(email)

    response = await client.post(
        "/api/v1/auth/request-email-change",
        headers=headers,
        json={"new_email": new_email, "password": "supersecret123"},
    )
    assert response.status_code == 204
    assert fake_email_sender.sent[-1]["to"] == new_email
    token = _extract_confirm_token(fake_email_sender.sent[-1]["body"])

    confirm = await client.post("/api/v1/auth/confirm-email-change", json={"token": token})
    assert confirm.status_code == 200
    assert confirm.json()["email"] == new_email

    old_login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "supersecret123"}
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        "/api/v1/auth/login", json={"email": new_email, "password": "supersecret123"}
    )
    assert new_login.status_code == 200


async def test_request_email_change_rejects_wrong_password() -> None:
    client, headers = await authed_client("changeemail-wrongpass@example.com")

    response = await client.post(
        "/api/v1/auth/request-email-change",
        headers=headers,
        json={"new_email": "someone-new@example.com", "password": "not-the-real-password"},
    )
    assert response.status_code == 401


async def test_request_email_change_rejects_already_registered_email() -> None:
    await authed_client("changeemail-taken-target@example.com")
    client, headers = await authed_client("changeemail-taken-requester@example.com")

    response = await client.post(
        "/api/v1/auth/request-email-change",
        headers=headers,
        json={"new_email": "changeemail-taken-target@example.com", "password": "supersecret123"},
    )
    assert response.status_code == 409


async def test_confirm_email_change_rejects_invalid_token() -> None:
    response = await (await authed_client("changeemail-invalid-token@example.com"))[0].post(
        "/api/v1/auth/confirm-email-change", json={"token": "not-a-real-token"}
    )
    assert response.status_code == 401


async def test_confirm_email_change_token_is_single_use() -> None:
    email = "changeemail-single-use@example.com"
    client, headers = await authed_client(email)

    await client.post(
        "/api/v1/auth/request-email-change",
        headers=headers,
        json={"new_email": "changeemail-single-use-new@example.com", "password": "supersecret123"},
    )
    token = _extract_confirm_token(fake_email_sender.sent[-1]["body"])

    first = await client.post("/api/v1/auth/confirm-email-change", json={"token": token})
    assert first.status_code == 200

    second = await client.post("/api/v1/auth/confirm-email-change", json={"token": token})
    assert second.status_code == 401
