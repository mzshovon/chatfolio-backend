from httpx import ASGITransport, AsyncClient

from chatfolio.main import app


async def _authed_client() -> tuple[AsyncClient, dict[str, str]]:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    email = "profile-owner@example.com"
    password = "supersecret123"
    await client.post("/v1/auth/register", json={"email": email, "password": password})
    login = await client.post("/v1/auth/login", json={"email": email, "password": password})
    token = login.json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


async def test_get_my_profile_auto_creates() -> None:
    client, headers = await _authed_client()
    response = await client.get("/v1/profiles/me", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "draft"
    assert body["full_name"] is None


async def test_update_my_profile() -> None:
    client, headers = await _authed_client()
    response = await client.patch(
        "/v1/profiles/me",
        headers=headers,
        json={"full_name": "Ada Lovelace", "title": "Backend Engineer"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Ada Lovelace"
    assert body["title"] == "Backend Engineer"


async def test_profile_is_isolated_per_user() -> None:
    client, headers = await _authed_client()
    other_email = "other-owner@example.com"
    await client.post(
        "/v1/auth/register", json={"email": other_email, "password": "supersecret123"}
    )
    other_login = await client.post(
        "/v1/auth/login", json={"email": other_email, "password": "supersecret123"}
    )
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}

    await client.patch("/v1/profiles/me", headers=headers, json={"full_name": "Owner One"})
    other_response = await client.get("/v1/profiles/me", headers=other_headers)
    assert other_response.json()["full_name"] is None


async def test_experience_crud() -> None:
    client, headers = await _authed_client()
    create_response = await client.post(
        "/v1/profiles/me/experience",
        headers=headers,
        json={"company": "Acme", "role": "Backend Engineer", "is_current": True},
    )
    assert create_response.status_code == 201
    experience_id = create_response.json()["id"]

    list_response = await client.get("/v1/profiles/me/experience", headers=headers)
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    update_response = await client.patch(
        f"/v1/profiles/me/experience/{experience_id}",
        headers=headers,
        json={"role": "Staff Engineer"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["role"] == "Staff Engineer"
    assert update_response.json()["company"] == "Acme"

    delete_response = await client.delete(
        f"/v1/profiles/me/experience/{experience_id}", headers=headers
    )
    assert delete_response.status_code == 204

    empty_list_response = await client.get("/v1/profiles/me/experience", headers=headers)
    assert empty_list_response.json() == []


async def test_cannot_access_another_users_experience() -> None:
    client, headers = await _authed_client()
    create_response = await client.post(
        "/v1/profiles/me/experience",
        headers=headers,
        json={"company": "Acme", "role": "Engineer"},
    )
    experience_id = create_response.json()["id"]

    other_email = "other-experience-owner@example.com"
    await client.post(
        "/v1/auth/register", json={"email": other_email, "password": "supersecret123"}
    )
    other_login = await client.post(
        "/v1/auth/login", json={"email": other_email, "password": "supersecret123"}
    )
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}

    response = await client.patch(
        f"/v1/profiles/me/experience/{experience_id}",
        headers=other_headers,
        json={"role": "Hacked"},
    )
    assert response.status_code == 404


async def test_project_skill_education_crud_smoke() -> None:
    client, headers = await _authed_client()
    project = await client.post(
        "/v1/profiles/me/projects",
        headers=headers,
        json={"title": "Chatfolio", "tech_stack": ["FastAPI", "Postgres"]},
    )
    assert project.status_code == 201
    assert project.json()["tech_stack"] == ["FastAPI", "Postgres"]

    skill = await client.post(
        "/v1/profiles/me/skills", headers=headers, json={"name": "Python", "category": "backend"}
    )
    assert skill.status_code == 201

    education = await client.post(
        "/v1/profiles/me/education",
        headers=headers,
        json={"institution": "MIT", "degree": "BSc Computer Science"},
    )
    assert education.status_code == 201
