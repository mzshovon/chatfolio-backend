import uuid
from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.config.settings import Settings, get_settings
from chatfolio.core.exceptions import ForbiddenError, UnauthorizedError
from chatfolio.core.security import decode_access_token
from chatfolio.db.session import get_db_session
from chatfolio.llm.base import LLMFactory
from chatfolio.llm.factory import LLMProviderFactory
from chatfolio.models.user import User, UserRole
from chatfolio.repositories.user_repository import UserRepository
from chatfolio.storage.base import StorageBackend
from chatfolio.storage.s3_storage import S3StorageBackend
from chatfolio.vectorstore.base import VectorStore
from chatfolio.vectorstore.chroma_store import ChromaVectorStore
from chatfolio.workers.queue import JobQueue, get_arq_pool

_bearer_scheme = HTTPBearer(auto_error=False)

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


def get_user_repository(session: DbSessionDep) -> UserRepository:
    return UserRepository(session)


UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]


def get_storage_backend(settings: SettingsDep) -> StorageBackend:
    return S3StorageBackend(settings.storage)


StorageBackendDep = Annotated[StorageBackend, Depends(get_storage_backend)]


async def get_job_queue() -> JobQueue:
    return await get_arq_pool()


JobQueueDep = Annotated[JobQueue, Depends(get_job_queue)]


def get_llm_provider_factory(settings: SettingsDep) -> LLMFactory:
    return LLMProviderFactory(settings.llm)


LLMFactoryDep = Annotated[LLMFactory, Depends(get_llm_provider_factory)]


def get_vector_store(settings: SettingsDep) -> VectorStore:
    return ChromaVectorStore(settings.vectorstore)


VectorStoreDep = Annotated[VectorStore, Depends(get_vector_store)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    settings: SettingsDep,
    repository: UserRepositoryDep,
) -> User:
    if credentials is None:
        raise UnauthorizedError("Missing bearer token.")

    try:
        payload = decode_access_token(credentials.credentials, settings.security)
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Access token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Invalid access token.") from exc

    user = await repository.get_by_id(uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise UnauthorizedError("Account no longer exists or is disabled.")

    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUserDep) -> User:
    if user.role != UserRole.ADMIN:
        raise ForbiddenError("Admin access required.")
    return user


AdminUserDep = Annotated[User, Depends(require_admin)]
