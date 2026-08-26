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
from tests.conftest import fake_email_sender
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
    client, _headers, slug = await publish_full_profile(
        "admin-metrics-owner@example.com", set_fake_llm, "admin-metrics-1"
    )
    await client.get(f"/api/v1/public/chatfolio/{slug}")  # records a PortfolioVisit
    admin_client, admin_headers = await _admin_client("admin-metrics-admin@example.com")

    response = await admin_client.get("/api/v1/admin/metrics", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["published_chatfolios"] >= 1
    assert body["total_users"] >= 2
    assert body["total_portfolio_visitors"] >= 1
    assert body["ai_tokens_monthly_quota"] >= 1_000_000
    assert "recruiters_engaged" in body
    assert "ai_tokens_used" in body


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


async def test_get_user_returns_single_row_by_id() -> None:
    client, _ = await authed_client("admin-getuser-target@example.com")
    admin_client, admin_headers = await _admin_client("admin-getuser-admin@example.com")

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(
            select(User).where(User.email == "admin-getuser-target@example.com")
        )
        user_id = result.scalar_one().id

    response = await admin_client.get(f"/api/v1/admin/users/{user_id}", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["email"] == "admin-getuser-target@example.com"


async def test_get_unknown_user_returns_404() -> None:
    admin_client, admin_headers = await _admin_client("admin-getuser-404-admin@example.com")
    response = await admin_client.get(
        f"/api/v1/admin/users/{uuid.uuid4()}", headers=admin_headers
    )
    assert response.status_code == 404


async def test_create_user_emails_a_temporary_password_and_writes_audit_log() -> None:
    admin_client, admin_headers = await _admin_client("admin-createuser-admin@example.com")

    response = await admin_client.post(
        "/api/v1/admin/users",
        headers=admin_headers,
        json={"email": "admin-created@example.com", "role": "candidate", "is_active": True},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "admin-created@example.com"
    assert "password" not in body

    assert fake_email_sender.sent[-1]["to"] == "admin-created@example.com"
    assert "Temporary password" in fake_email_sender.sent[-1]["body"]

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(
            select(AdminAuditLog).where(AdminAuditLog.action == "user.create")
        )
        assert result.scalars().first() is not None


async def test_create_user_rejects_duplicate_email() -> None:
    await authed_client("admin-createuser-dupe@example.com")
    admin_client, admin_headers = await _admin_client("admin-createuser-dupe-admin@example.com")

    response = await admin_client.post(
        "/api/v1/admin/users",
        headers=admin_headers,
        json={"email": "admin-createuser-dupe@example.com"},
    )
    assert response.status_code == 409


async def test_update_user_bans_by_setting_is_active_false() -> None:
    client, _ = await authed_client("admin-updateuser-target@example.com")
    admin_client, admin_headers = await _admin_client("admin-updateuser-admin@example.com")

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(
            select(User).where(User.email == "admin-updateuser-target@example.com")
        )
        user_id = result.scalar_one().id

    response = await admin_client.patch(
        f"/api/v1/admin/users/{user_id}", headers=admin_headers, json={"is_active": False}
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    banned_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin-updateuser-target@example.com", "password": "supersecret123"},
    )
    assert banned_login.status_code == 401


async def test_delete_user_removes_the_account() -> None:
    await authed_client("admin-deleteuser-target@example.com")
    admin_client, admin_headers = await _admin_client("admin-deleteuser-admin@example.com")

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(
            select(User).where(User.email == "admin-deleteuser-target@example.com")
        )
        user_id = result.scalar_one().id

    response = await admin_client.delete(
        f"/api/v1/admin/users/{user_id}", headers=admin_headers
    )
    assert response.status_code == 204

    follow_up = await admin_client.get(
        f"/api/v1/admin/users/{user_id}", headers=admin_headers
    )
    assert follow_up.status_code == 404


async def test_admin_cannot_delete_own_account() -> None:
    admin_client, admin_headers = await _admin_client("admin-selfdelete@example.com")

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(
            select(User).where(User.email == "admin-selfdelete@example.com")
        )
        admin_id = result.scalar_one().id

    response = await admin_client.delete(
        f"/api/v1/admin/users/{admin_id}", headers=admin_headers
    )
    assert response.status_code == 422


async def test_role_crud_lifecycle() -> None:
    admin_client, admin_headers = await _admin_client("admin-role-crud@example.com")

    create = await admin_client.post(
        "/api/v1/admin/roles",
        headers=admin_headers,
        json={
            "name": "Reviewer",
            "description": "Read-only access to chatfolios and metrics.",
            "permissions": ["chatfolios.view", "metrics.view"],
        },
    )
    assert create.status_code == 201
    role_id = create.json()["id"]

    duplicate = await admin_client.post(
        "/api/v1/admin/roles", headers=admin_headers, json={"name": "Reviewer"}
    )
    assert duplicate.status_code == 409

    listed = await admin_client.get("/api/v1/admin/roles", headers=admin_headers)
    assert any(r["id"] == role_id for r in listed.json())

    update = await admin_client.patch(
        f"/api/v1/admin/roles/{role_id}",
        headers=admin_headers,
        json={"permissions": ["chatfolios.view", "metrics.view", "cvjobs.retry"]},
    )
    assert update.status_code == 200
    assert update.json()["permissions"] == ["chatfolios.view", "metrics.view", "cvjobs.retry"]

    delete = await admin_client.delete(f"/api/v1/admin/roles/{role_id}", headers=admin_headers)
    assert delete.status_code == 204

    after_delete = await admin_client.get("/api/v1/admin/roles", headers=admin_headers)
    assert not any(r["id"] == role_id for r in after_delete.json())


async def test_permission_crud_lifecycle_and_key_immutability() -> None:
    admin_client, admin_headers = await _admin_client("admin-permission-crud@example.com")

    create = await admin_client.post(
        "/api/v1/admin/permissions",
        headers=admin_headers,
        json={"key": "chatfolios.unpublish", "description": "Unpublish any chatfolio."},
    )
    assert create.status_code == 201
    permission_id = create.json()["id"]
    assert create.json()["used_by_roles_count"] == 0

    bad_key = await admin_client.post(
        "/api/v1/admin/permissions",
        headers=admin_headers,
        json={"key": "not a valid key!!", "description": "x"},
    )
    assert bad_key.status_code == 422

    duplicate = await admin_client.post(
        "/api/v1/admin/permissions",
        headers=admin_headers,
        json={"key": "chatfolios.unpublish", "description": "dupe"},
    )
    assert duplicate.status_code == 409

    update = await admin_client.patch(
        f"/api/v1/admin/permissions/{permission_id}",
        headers=admin_headers,
        json={"description": "Unpublish any candidate's chatfolio."},
    )
    assert update.status_code == 200
    assert update.json()["description"] == "Unpublish any candidate's chatfolio."

    delete = await admin_client.delete(
        f"/api/v1/admin/permissions/{permission_id}", headers=admin_headers
    )
    assert delete.status_code == 204


async def test_deleting_permission_strips_it_from_roles_that_grant_it() -> None:
    admin_client, admin_headers = await _admin_client("admin-permission-strip@example.com")

    permission = await admin_client.post(
        "/api/v1/admin/permissions",
        headers=admin_headers,
        json={"key": "cvjobs.retry", "description": "Retry a failed CV job."},
    )
    permission_id = permission.json()["id"]

    role = await admin_client.post(
        "/api/v1/admin/roles",
        headers=admin_headers,
        json={"name": "Ops", "permissions": ["cvjobs.retry", "metrics.view"]},
    )
    role_id = role.json()["id"]

    used = await admin_client.get("/api/v1/admin/permissions", headers=admin_headers)
    matching = next(p for p in used.json() if p["id"] == permission_id)
    assert matching["used_by_roles_count"] == 1

    await admin_client.delete(f"/api/v1/admin/permissions/{permission_id}", headers=admin_headers)

    roles = await admin_client.get("/api/v1/admin/roles", headers=admin_headers)
    updated_role = next(r for r in roles.json() if r["id"] == role_id)
    assert updated_role["permissions"] == ["metrics.view"]
