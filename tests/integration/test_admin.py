import uuid
from collections.abc import AsyncGenerator, Callable
from typing import Any

import pytest
from sqlalchemy import select

from chatfolio.api.deps import get_job_queue, get_llm_provider_factory
from chatfolio.db.session import get_sessionmaker
from chatfolio.main import app
from chatfolio.models.audit_log import AdminAuditLog
from chatfolio.models.cv import CVStatus, UploadedCV
from chatfolio.models.profile import CandidateProfile
from chatfolio.models.user import User, UserRole
from tests.factories.fake_llm import FakeLLMFactory
from tests.factories.publish_flow import authed_client, publish_full_profile


@pytest.fixture
def set_fake_llm() -> Callable[[str], None]:
    def _set(response: str) -> None:
        app.dependency_overrides[get_llm_provider_factory] = lambda: FakeLLMFactory(response)

    yield _set
    app.dependency_overrides.pop(get_llm_provider_factory, None)


class SpyJobQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, tuple[object, ...]]] = []

    async def enqueue_job(self, function: str, *args: object) -> None:
        self.enqueued.append((function, args))


@pytest.fixture
async def spy_job_queue() -> AsyncGenerator[SpyJobQueue]:
    spy = SpyJobQueue()
    app.dependency_overrides[get_job_queue] = lambda: spy
    yield spy
    app.dependency_overrides.pop(get_job_queue, None)


async def _promote_to_admin(email: str) -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        user.role = UserRole.ADMIN
        await session.commit()


async def _admin_client(email: str) -> tuple[Any, dict[str, str]]:
    client, headers = await authed_client(email)
    await _promote_to_admin(email)
    return client, headers


async def _get_or_create_profile(email: str) -> CandidateProfile:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        profile_result = await session.execute(
            select(CandidateProfile).where(CandidateProfile.user_id == user.id)
        )
        profile = profile_result.scalar_one_or_none()
        if profile is None:
            profile = CandidateProfile(user_id=user.id)
            session.add(profile)
            await session.commit()
        return profile


async def _create_failed_cv(profile_email: str) -> uuid.UUID:
    profile = await _get_or_create_profile(profile_email)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        cv = UploadedCV(
            profile_id=profile.id,
            file_key="cvs/test.pdf",
            file_type="pdf",
            size_bytes=100,
            status=CVStatus.FAILED,
            error_message="parse failed",
        )
        session.add(cv)
        await session.commit()
        return cv.id


async def test_admin_endpoints_reject_non_admin_users() -> None:
    client, headers = await authed_client("admin-reject@example.com")
    response = await client.get("/api/v1/admin/users", headers=headers)
    assert response.status_code == 403


async def test_admin_endpoints_require_auth() -> None:
    client, _ = await authed_client("admin-noauth@example.com")
    response = await client.get("/api/v1/admin/users")
    assert response.status_code == 401


async def test_list_users_returns_all_registered_users() -> None:
    await authed_client("admin-list-candidate@example.com")
    admin_client, admin_headers = await _admin_client("admin-list-admin@example.com")

    response = await admin_client.get("/api/v1/admin/users", headers=admin_headers)
    assert response.status_code == 200
    emails = {u["email"] for u in response.json()}
    assert "admin-list-candidate@example.com" in emails
    assert "admin-list-admin@example.com" in emails


async def test_list_chatfolios_filters_by_published(set_fake_llm: Any) -> None:
    _, _headers, slug = await publish_full_profile(
        "admin-chatfolio-owner@example.com", set_fake_llm, "admin-cf-1"
    )
    admin_client, admin_headers = await _admin_client("admin-chatfolio-admin@example.com")

    published = await admin_client.get(
        "/api/v1/admin/chatfolios", params={"is_published": True}, headers=admin_headers
    )
    assert published.status_code == 200
    slugs = {cf["slug"] for cf in published.json()}
    assert slug in slugs
    matching = next(cf for cf in published.json() if cf["slug"] == slug)
    assert matching["owner_email"] == "admin-chatfolio-owner@example.com"


async def test_metrics_reflect_real_counts(set_fake_llm: Any) -> None:
    await publish_full_profile("admin-metrics-owner@example.com", set_fake_llm, "admin-metrics-1")
    admin_client, admin_headers = await _admin_client("admin-metrics-admin@example.com")

    response = await admin_client.get("/api/v1/admin/metrics", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["published_chatfolios"] >= 1
    assert body["total_users"] >= 2


async def test_list_failed_cv_jobs_and_retry(spy_job_queue: SpyJobQueue) -> None:
    await authed_client("admin-cv-owner@example.com")
    cv_id = await _create_failed_cv("admin-cv-owner@example.com")
    admin_client, admin_headers = await _admin_client("admin-cv-admin@example.com")

    failed = await admin_client.get("/api/v1/admin/cv-jobs/failed", headers=admin_headers)
    assert failed.status_code == 200
    assert any(job["id"] == str(cv_id) for job in failed.json())

    retry = await admin_client.post(f"/api/v1/admin/cv-jobs/{cv_id}/retry", headers=admin_headers)
    assert retry.status_code == 200
    assert retry.json()["status"] == "pending"
    assert ("parse_cv_job", (str(cv_id),)) in spy_job_queue.enqueued


async def test_retry_non_failed_cv_job_is_rejected() -> None:
    await authed_client("admin-cv-notfailed-owner@example.com")
    profile = await _get_or_create_profile("admin-cv-notfailed-owner@example.com")
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        cv = UploadedCV(
            profile_id=profile.id,
            file_key="cvs/test.pdf",
            file_type="pdf",
            size_bytes=100,
            status=CVStatus.PARSED,
        )
        session.add(cv)
        await session.commit()
        cv_id = cv.id

    admin_client, admin_headers = await _admin_client("admin-cv-notfailed-admin@example.com")
    response = await admin_client.post(
        f"/api/v1/admin/cv-jobs/{cv_id}/retry", headers=admin_headers
    )
    assert response.status_code == 422


async def test_unpublish_chatfolio_writes_audit_log(set_fake_llm: Any) -> None:
    _, _headers, slug = await publish_full_profile(
        "admin-unpublish-owner@example.com", set_fake_llm, "admin-unpub-1"
    )
    admin_client, admin_headers = await _admin_client("admin-unpublish-admin@example.com")

    chatfolios = await admin_client.get(
        "/api/v1/admin/chatfolios", params={"is_published": True}, headers=admin_headers
    )
    chatfolio_id = next(cf["id"] for cf in chatfolios.json() if cf["slug"] == slug)

    response = await admin_client.post(
        f"/api/v1/admin/chatfolios/{chatfolio_id}/unpublish", headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["is_published"] is False

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(
            select(AdminAuditLog).where(
                AdminAuditLog.target_type == "public_chatfolio",
                AdminAuditLog.target_id == chatfolio_id,
            )
        )
        log_entry = result.scalar_one()
        assert log_entry.action == "chatfolio.unpublish"


async def test_public_chatfolio_no_longer_reachable_after_admin_unpublish(
    set_fake_llm: Any,
) -> None:
    owner_client, _owner_headers, slug = await publish_full_profile(
        "admin-unpublish-effect-owner@example.com", set_fake_llm, "admin-unpub-2"
    )
    admin_client, admin_headers = await _admin_client("admin-unpublish-effect-admin@example.com")

    chatfolios = await admin_client.get(
        "/api/v1/admin/chatfolios", params={"is_published": True}, headers=admin_headers
    )
    chatfolio_id = next(cf["id"] for cf in chatfolios.json() if cf["slug"] == slug)
    await admin_client.post(
        f"/api/v1/admin/chatfolios/{chatfolio_id}/unpublish", headers=admin_headers
    )

    response = await owner_client.get(f"/api/v1/public/chatfolio/{slug}")
    assert response.status_code == 404
