from fastapi import APIRouter, Request, status

from chatfolio.api.deps import (
    CurrentUserDep,
    EmailSenderDep,
    SettingsDep,
    SmsSenderDep,
    UserRepositoryDep,
)
from chatfolio.core.rate_limit import limiter
from chatfolio.schemas.auth import (
    ChangePasswordRequest,
    ConfirmEmailChangeRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    RequestEmailChangeRequest,
    ResetPasswordRequest,
    TokenResponse,
    TwoFactorChallengeResponse,
    TwoFactorLoginVerifyRequest,
    TwoFactorResendRequest,
    TwoFactorSetupRequest,
    TwoFactorSetupResponse,
    TwoFactorVerifySetupRequest,
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


@router.post("/login", response_model=TokenResponse | TwoFactorChallengeResponse)
@limiter.limit("10/minute")
async def login(
    request: Request,
    payload: LoginRequest,
    repository: UserRepositoryDep,
    settings: SettingsDep,
    email_sender: EmailSenderDep,
    sms_sender: SmsSenderDep,
) -> TokenResponse | TwoFactorChallengeResponse:
    return await _service(repository, settings).login(
        payload.email, payload.password, email_sender=email_sender, sms_sender=sms_sender
    )


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


@router.post("/forgot-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def forgot_password(
    request: Request,
    payload: ForgotPasswordRequest,
    repository: UserRepositoryDep,
    settings: SettingsDep,
    email_sender: EmailSenderDep,
) -> None:
    # Always 204, whether or not the email is registered — see AuthService.forgot_password.
    await _service(repository, settings).forgot_password(
        payload.email, email_sender=email_sender, frontend_base_url=settings.app.frontend_base_url
    )


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def reset_password(
    request: Request,
    payload: ResetPasswordRequest,
    repository: UserRepositoryDep,
    settings: SettingsDep,
) -> None:
    await _service(repository, settings).reset_password(payload.token, payload.new_password)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    payload: ChangePasswordRequest,
    current_user: CurrentUserDep,
    repository: UserRepositoryDep,
    settings: SettingsDep,
) -> None:
    await _service(repository, settings).change_password(
        current_user, payload.current_password, payload.new_password
    )


@router.post("/request-email-change", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def request_email_change(
    request: Request,
    payload: RequestEmailChangeRequest,
    current_user: CurrentUserDep,
    repository: UserRepositoryDep,
    settings: SettingsDep,
    email_sender: EmailSenderDep,
) -> None:
    await _service(repository, settings).request_email_change(
        current_user,
        payload.new_email,
        payload.password,
        email_sender=email_sender,
        frontend_base_url=settings.app.frontend_base_url,
    )


@router.post("/confirm-email-change", response_model=UserResponse)
@limiter.limit("10/minute")
async def confirm_email_change(
    request: Request,
    payload: ConfirmEmailChangeRequest,
    repository: UserRepositoryDep,
    settings: SettingsDep,
) -> UserResponse:
    user = await _service(repository, settings).confirm_email_change(payload.token)
    return UserResponse.model_validate(user)


@router.post("/2fa/setup", response_model=TwoFactorSetupResponse)
@limiter.limit("5/minute")
async def setup_two_factor(
    request: Request,
    payload: TwoFactorSetupRequest,
    current_user: CurrentUserDep,
    repository: UserRepositoryDep,
    settings: SettingsDep,
    email_sender: EmailSenderDep,
    sms_sender: SmsSenderDep,
) -> TwoFactorSetupResponse:
    return await _service(repository, settings).setup_two_factor(
        current_user,
        payload.method,
        payload.phone,
        email_sender=email_sender,
        sms_sender=sms_sender,
    )


@router.post("/2fa/verify-setup", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
async def verify_two_factor_setup(
    request: Request,
    payload: TwoFactorVerifySetupRequest,
    current_user: CurrentUserDep,
    repository: UserRepositoryDep,
    settings: SettingsDep,
) -> None:
    await _service(repository, settings).verify_two_factor_setup(current_user, payload.code)


@router.post("/2fa/login/verify", response_model=TokenResponse)
@limiter.limit("10/minute")
async def verify_two_factor_login(
    request: Request,
    payload: TwoFactorLoginVerifyRequest,
    repository: UserRepositoryDep,
    settings: SettingsDep,
) -> TokenResponse:
    return await _service(repository, settings).verify_two_factor_login(
        payload.challenge_token, payload.code
    )


@router.post("/2fa/login/resend", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("3/minute")
async def resend_two_factor_login_code(
    request: Request,
    payload: TwoFactorResendRequest,
    repository: UserRepositoryDep,
    settings: SettingsDep,
    email_sender: EmailSenderDep,
    sms_sender: SmsSenderDep,
) -> None:
    await _service(repository, settings).resend_two_factor_login_code(
        payload.challenge_token, email_sender=email_sender, sms_sender=sms_sender
    )
