import uuid

from fastapi import APIRouter, File, UploadFile, status

from chatfolio.api.deps import (
    CurrentUserDep,
    DbSessionDep,
    JobQueueDep,
    SettingsDep,
    StorageBackendDep,
)
from chatfolio.repositories.profile_repository import ProfileRepository
from chatfolio.schemas.cv import CVResponse
from chatfolio.services.cv_service import CVService
from chatfolio.services.profile_service import ProfileService

router = APIRouter(prefix="/cv", tags=["cv"])


def _service(
    session: DbSessionDep, storage: StorageBackendDep, settings: SettingsDep, job_queue: JobQueueDep
) -> CVService:
    profile_service = ProfileService(ProfileRepository(session))
    return CVService(profile_service, storage, settings.storage, job_queue)


@router.post("/upload", response_model=CVResponse, status_code=status.HTTP_201_CREATED)
async def upload_cv(
    current_user: CurrentUserDep,
    session: DbSessionDep,
    storage: StorageBackendDep,
    settings: SettingsDep,
    job_queue: JobQueueDep,
    file: UploadFile = File(...),  # noqa: B008 (FastAPI's documented pattern for file uploads)
) -> CVResponse:
    content = await file.read()
    cv = await _service(session, storage, settings, job_queue).upload(
        current_user, file.filename or "", file.content_type, content
    )
    return CVResponse.model_validate(cv)


@router.get("/{cv_id}/status", response_model=CVResponse)
async def get_cv_status(
    cv_id: uuid.UUID,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    storage: StorageBackendDep,
    settings: SettingsDep,
    job_queue: JobQueueDep,
) -> CVResponse:
    cv = await _service(session, storage, settings, job_queue).get_status(current_user, cv_id)
    return CVResponse.model_validate(cv)


@router.post("/{cv_id}/retry", response_model=CVResponse)
async def retry_cv(
    cv_id: uuid.UUID,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    storage: StorageBackendDep,
    settings: SettingsDep,
    job_queue: JobQueueDep,
) -> CVResponse:
    cv = await _service(session, storage, settings, job_queue).retry(current_user, cv_id)
    return CVResponse.model_validate(cv)
