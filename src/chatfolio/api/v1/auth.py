from fastapi import APIRouter, Request, status

from chatfolio.api.deps import CurrentUserDep, SettingsDep, UserRepositoryDep
from chatfolio.core.rate_limit import limiter
from chatfolio.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from chatfolio.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _service(repository: UserRepositoryDep, settings: SettingsDep) -> AuthService:
    return AuthService(repository, settings.security)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(
    request: Request,
    payload: RegisterRequest,
    repository: UserRepositoryDep,
    settings: SettingsDep,
) -> UserResponse:
    user = await _service(repository, settings).register(payload.email, payload.password)
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(
    request: Request, payload: LoginRequest, repository: UserRepositoryDep, settings: SettingsDep
) -> TokenResponse:
    return await _service(repository, settings).login(payload.email, payload.password)


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("20/minute")
async def refresh(
    request: Request,
    payload: RefreshRequest,
    repository: UserRepositoryDep,
    settings: SettingsDep,
) -> TokenResponse:
    return await _service(repository, settings).refresh(payload.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/minute")
async def logout(
    request: Request, payload: RefreshRequest, repository: UserRepositoryDep, settings: SettingsDep
) -> None:
    await _service(repository, settings).logout(payload.refresh_token)


@router.get("/me", response_model=UserResponse)
async def me(current_user: CurrentUserDep) -> UserResponse:
    return UserResponse.model_validate(current_user)
