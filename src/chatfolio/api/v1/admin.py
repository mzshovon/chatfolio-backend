import uuid

from fastapi import APIRouter, Query

from chatfolio.api.deps import AdminUserDep, DbSessionDep, JobQueueDep
from chatfolio.models.chatfolio import PublicChatfolio
from chatfolio.models.cv import UploadedCV
from chatfolio.schemas.admin import AdminChatfolioResponse, AdminCVJobResponse, AdminMetricsResponse
from chatfolio.schemas.auth import UserResponse
from chatfolio.services.admin_service import AdminService

router = APIRouter(prefix="/admin", tags=["admin"])


def _service(session: DbSessionDep, job_queue: JobQueueDep) -> AdminService:
    return AdminService(session, job_queue)


def _to_chatfolio_response(chatfolio: PublicChatfolio, owner_email: str) -> AdminChatfolioResponse:
    return AdminChatfolioResponse(
        id=chatfolio.id,
        slug=chatfolio.slug,
        is_published=chatfolio.is_published,
        published_at=chatfolio.published_at,
        owner_email=owner_email,
    )


def _to_cv_job_response(cv: UploadedCV, owner_email: str) -> AdminCVJobResponse:
    return AdminCVJobResponse(
        id=cv.id,
        status=cv.status,
        error_message=cv.error_message,
        owner_email=owner_email,
        created_at=cv.created_at,
    )


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    current_user: AdminUserDep,
    session: DbSessionDep,
    job_queue: JobQueueDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[UserResponse]:
    users = await _service(session, job_queue).list_users(limit=limit, offset=offset)
    return [UserResponse.model_validate(user) for user in users]


@router.get("/chatfolios", response_model=list[AdminChatfolioResponse])
async def list_chatfolios(
    current_user: AdminUserDep,
    session: DbSessionDep,
    job_queue: JobQueueDep,
    is_published: bool | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[AdminChatfolioResponse]:
    pairs = await _service(session, job_queue).list_chatfolios(
        is_published=is_published, limit=limit, offset=offset
    )
    return [_to_chatfolio_response(chatfolio, email) for chatfolio, email in pairs]


@router.get("/metrics", response_model=AdminMetricsResponse)
async def get_metrics(
    current_user: AdminUserDep, session: DbSessionDep, job_queue: JobQueueDep
) -> AdminMetricsResponse:
    metrics = await _service(session, job_queue).get_metrics()
    return AdminMetricsResponse(**metrics)


@router.get("/cv-jobs/failed", response_model=list[AdminCVJobResponse])
async def list_failed_cv_jobs(
    current_user: AdminUserDep,
    session: DbSessionDep,
    job_queue: JobQueueDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[AdminCVJobResponse]:
    pairs = await _service(session, job_queue).list_failed_cv_jobs(limit=limit, offset=offset)
    return [_to_cv_job_response(cv, email) for cv, email in pairs]


@router.post("/cv-jobs/{cv_id}/retry", response_model=AdminCVJobResponse)
async def retry_cv_job(
    cv_id: uuid.UUID, current_user: AdminUserDep, session: DbSessionDep, job_queue: JobQueueDep
) -> AdminCVJobResponse:
    cv, owner_email = await _service(session, job_queue).retry_cv_job(current_user, cv_id)
    return _to_cv_job_response(cv, owner_email)


@router.post("/chatfolios/{chatfolio_id}/unpublish", response_model=AdminChatfolioResponse)
async def unpublish_chatfolio(
    chatfolio_id: uuid.UUID,
    current_user: AdminUserDep,
    session: DbSessionDep,
    job_queue: JobQueueDep,
) -> AdminChatfolioResponse:
    chatfolio, owner_email = await _service(session, job_queue).unpublish_chatfolio(
        current_user, chatfolio_id
    )
    return _to_chatfolio_response(chatfolio, owner_email)
