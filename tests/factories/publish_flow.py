from collections.abc import Callable

from httpx import ASGITransport, AsyncClient

from chatfolio.main import app


async def authed_client(email: str) -> tuple[AsyncClient, dict[str, str]]:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    password = "supersecret123"
    await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = login.json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


async def publish_full_profile(
    email: str, set_fake_llm: Callable[[str], None], slug: str
) -> tuple[AsyncClient, dict[str, str], str]:
    """Registers a candidate, fills in enough data to publish, approves both sections, sets the
    given slug, and publishes. Shared by public-portfolio and chat tests, which both need a
    real published Chatfolio to exercise their read paths against."""
    client, headers = await authed_client(email)
    await client.patch(
        "/api/v1/profiles/me",
        headers=headers,
        json={"full_name": "Ada Lovelace", "title": "Backend Engineer"},
    )
    await client.post(
        "/api/v1/profiles/me/experience",
        headers=headers,
        json={"company": "Acme", "role": "Engineer", "is_current": True},
    )

    set_fake_llm("Generated content.")
    sections = (await client.get("/api/v1/sections", headers=headers)).json()
    for section in sections:
        await client.post(f"/api/v1/sections/{section['id']}/approve", headers=headers)

    await client.patch("/api/v1/portfolio-settings", headers=headers, json={"slug": slug})
    publish_response = await client.post("/api/v1/portfolio-settings/publish", headers=headers)
    assert publish_response.status_code == 200
    return client, headers, slug
