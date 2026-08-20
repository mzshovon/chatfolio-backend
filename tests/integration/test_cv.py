from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from chatfolio.db.session import get_sessionmaker
from chatfolio.main import app
from chatfolio.models.cv import CVStatus, UploadedCV

FAKE_PDF = b"%PDF-1.4\n%fake pdf content for tests\n"


async def _authed_client(email: str = "cv-owner@example.com") -> tuple[AsyncClient, dict[str, str]]:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    password = "supersecret123"
    await client.post("/v1/auth/register", json={"email": email, "password": password})
    login = await client.post("/v1/auth/login", json={"email": email, "password": password})
    token = login.json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


async def _mark_failed(cv_id: str) -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        await session.execute(
            update(UploadedCV)
            .where(UploadedCV.id == cv_id)
            .values(status=CVStatus.FAILED, error_message="Could not extract text.")
        )
        await session.commit()


async def test_upload_valid_pdf() -> None:
    client, headers = await _authed_client()
    response = await client.post(
        "/v1/cv/upload",
        headers=headers,
        files={"file": ("resume.pdf", FAKE_PDF, "application/pdf")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["file_type"] == "pdf"
    assert body["size_bytes"] == len(FAKE_PDF)


async def test_upload_rejects_unsupported_type() -> None:
    client, headers = await _authed_client()
    response = await client.post(
        "/v1/cv/upload",
        headers=headers,
        files={"file": ("resume.txt", b"plain text resume", "text/plain")},
    )
    assert response.status_code == 422


async def test_upload_rejects_oversized_file() -> None:
    client, headers = await _authed_client()
    oversized = b"0" * (21 * 1024 * 1024)
    response = await client.post(
        "/v1/cv/upload",
        headers=headers,
        files={"file": ("resume.pdf", oversized, "application/pdf")},
    )
    assert response.status_code == 422


async def test_upload_rejects_empty_file() -> None:
    client, headers = await _authed_client()
    response = await client.post(
        "/v1/cv/upload",
        headers=headers,
        files={"file": ("resume.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 422


async def test_get_status_and_retry_after_failure() -> None:
    client, headers = await _authed_client()
    upload_response = await client.post(
        "/v1/cv/upload",
        headers=headers,
        files={"file": ("resume.pdf", FAKE_PDF, "application/pdf")},
    )
    cv_id = upload_response.json()["id"]

    status_response = await client.get(f"/v1/cv/{cv_id}/status", headers=headers)
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "pending"

    retry_before_failure = await client.post(f"/v1/cv/{cv_id}/retry", headers=headers)
    assert retry_before_failure.status_code == 422

    await _mark_failed(cv_id)

    failed_status_response = await client.get(f"/v1/cv/{cv_id}/status", headers=headers)
    assert failed_status_response.json()["status"] == "failed"
    assert failed_status_response.json()["error_message"] == "Could not extract text."

    retry_response = await client.post(f"/v1/cv/{cv_id}/retry", headers=headers)
    assert retry_response.status_code == 200
    assert retry_response.json()["status"] == "pending"
    assert retry_response.json()["error_message"] is None


async def test_cannot_access_another_users_cv() -> None:
    client, headers = await _authed_client("cv-owner-a@example.com")
    upload_response = await client.post(
        "/v1/cv/upload",
        headers=headers,
        files={"file": ("resume.pdf", FAKE_PDF, "application/pdf")},
    )
    cv_id = upload_response.json()["id"]

    _, other_headers = await _authed_client("cv-owner-b@example.com")
    response = await client.get(f"/v1/cv/{cv_id}/status", headers=other_headers)
    assert response.status_code == 404
