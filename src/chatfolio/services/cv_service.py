import uuid

from chatfolio.config.settings import StorageSettings
from chatfolio.core.exceptions import ValidationFailedError
from chatfolio.models.cv import CVStatus, UploadedCV
from chatfolio.models.user import User
from chatfolio.services.profile_service import ProfileService
from chatfolio.storage.base import StorageBackend
from chatfolio.workers.queue import JobQueue

ALLOWED_CV_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}
ALLOWED_CV_EXTENSIONS = set(ALLOWED_CV_CONTENT_TYPES.values())


class CVService:
    def __init__(
        self,
        profile_service: ProfileService,
        storage: StorageBackend,
        storage_settings: StorageSettings,
        job_queue: JobQueue,
    ) -> None:
        self._profile_service = profile_service
        self._storage = storage
        self._settings = storage_settings
        self._job_queue = job_queue

    async def upload(
        self, user: User, filename: str, content_type: str | None, content: bytes
    ) -> UploadedCV:
        extension = self._resolve_extension(filename, content_type)
        if extension is None:
            raise ValidationFailedError("Only PDF, DOC, and DOCX files are accepted.")

        max_bytes = self._settings.max_cv_upload_mb * 1024 * 1024
        if len(content) > max_bytes:
            limit = self._settings.max_cv_upload_mb
            raise ValidationFailedError(f"File exceeds the {limit}MB limit.")
        if len(content) == 0:
            raise ValidationFailedError("Uploaded file is empty.")

        profile = await self._profile_service.get_or_create_for_user(user)
        key = f"cvs/{profile.id}/{uuid.uuid4()}{extension}"
        await self._storage.upload(
            key=key, content=content, content_type=content_type or "application/octet-stream"
        )

        cv = UploadedCV(
            file_key=key,
            file_type=extension.removeprefix("."),
            size_bytes=len(content),
            status=CVStatus.PENDING,
        )
        cv = await self._profile_service.add_child(user, cv)
        await self._job_queue.enqueue_job("parse_cv_job", str(cv.id))
        return cv

    async def get_status(self, user: User, cv_id: uuid.UUID) -> UploadedCV:
        return await self._profile_service.get_owned_child(user, UploadedCV, cv_id)

    async def retry(self, user: User, cv_id: uuid.UUID) -> UploadedCV:
        cv = await self._profile_service.get_owned_child(user, UploadedCV, cv_id)
        if cv.status != CVStatus.FAILED:
            raise ValidationFailedError("Only a failed upload can be retried.")
        cv = await self._profile_service.update_child(
            user, UploadedCV, cv_id, {"status": CVStatus.PENDING, "error_message": None}
        )
        await self._job_queue.enqueue_job("parse_cv_job", str(cv.id))
        return cv

    @staticmethod
    def _resolve_extension(filename: str, content_type: str | None) -> str | None:
        if content_type in ALLOWED_CV_CONTENT_TYPES:
            return ALLOWED_CV_CONTENT_TYPES[content_type]
        if "." in filename:
            suffix = "." + filename.rsplit(".", 1)[-1].lower()
            if suffix in ALLOWED_CV_EXTENSIONS:
                return suffix
        return None
