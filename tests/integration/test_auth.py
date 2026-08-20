from httpx import ASGITransport, AsyncClient

from chatfolio.main import app


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_register_login_me_refresh_logout() -> None:
    async with await _client() as client:
        email = "candidate@example.com"
        password = "supersecret123"

        register_response = await client.post(
            "/v1/auth/register", json={"email": email, "password": password}
        )
        assert register_response.status_code == 201
        assert register_response.json()["email"] == email
        assert register_response.json()["role"] == "candidate"

        duplicate_response = await client.post(
            "/v1/auth/register", json={"email": email, "password": password}
        )
        assert duplicate_response.status_code == 409

        login_response = await client.post(
            "/v1/auth/login", json={"email": email, "password": password}
        )
        assert login_response.status_code == 200
        tokens = login_response.json()
        assert tokens["access_token"] and tokens["refresh_token"]

        bad_login_response = await client.post(
            "/v1/auth/login", json={"email": email, "password": "wrong-password"}
        )
        assert bad_login_response.status_code == 401

        me_response = await client.get(
            "/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        assert me_response.status_code == 200
        assert me_response.json()["email"] == email

        unauthenticated_response = await client.get("/v1/auth/me")
        assert unauthenticated_response.status_code == 401

        refresh_response = await client.post(
            "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert refresh_response.status_code == 200
        new_tokens = refresh_response.json()
        assert new_tokens["refresh_token"] != tokens["refresh_token"]

        reused_refresh_response = await client.post(
            "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert reused_refresh_response.status_code == 401

        logout_response = await client.post(
            "/v1/auth/logout", json={"refresh_token": new_tokens["refresh_token"]}
        )
        assert logout_response.status_code == 204

        revoked_refresh_response = await client.post(
            "/v1/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
        )
        assert revoked_refresh_response.status_code == 401
