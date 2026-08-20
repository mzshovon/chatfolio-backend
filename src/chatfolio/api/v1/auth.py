from fastapi import APIRouter, status

from chatfolio.api.deps import CurrentUserDep, SettingsDep, UserRepositoryDep
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
async def register(
    payload: RegisterRequest, repository: UserRepositoryDep, settings: SettingsDep
) -> UserResponse:
    user = await _service(repository, settings).register(payload.email, payload.password)
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest, repository: UserRepositoryDep, settings: SettingsDep
) -> TokenResponse:
    return await _service(repository, settings).login(payload.email, payload.password)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest, repository: UserRepositoryDep, settings: SettingsDep
) -> TokenResponse:
    return await _service(repository, settings).refresh(payload.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: RefreshRequest, repository: UserRepositoryDep, settings: SettingsDep
) -> None:
    await _service(repository, settings).logout(payload.refresh_token)


@router.get("/me", response_model=UserResponse)
async def me(current_user: CurrentUserDep) -> UserResponse:
    return UserResponse.model_validate(current_user)
