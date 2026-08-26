import uuid
from datetime import UTC, datetime, timedelta

import jwt

from chatfolio.config.settings import SecuritySettings
from chatfolio.core.exceptions import ConflictError, UnauthorizedError, ValidationFailedError
from chatfolio.core.security import (
    create_access_token,
    create_two_factor_challenge_token,
    decode_two_factor_challenge_token,
    generate_opaque_token,
    generate_otp_code,
    hash_opaque_token,
    hash_password,
    verify_password,
)
from chatfolio.models.user import (
    EmailChangeRequest,
    OtpCode,
    OtpPurpose,
    PasswordResetToken,
    RefreshToken,
    TwoFactorMethod,
    User,
    UserRole,
)
from chatfolio.notifications.base import EmailSender, SmsSender
from chatfolio.repositories.user_repository import UserRepository
from chatfolio.schemas.auth import TokenResponse, TwoFactorChallengeResponse, TwoFactorSetupResponse


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[0]}{'•' * 3}@{domain}"


def _mask_phone(phone: str) -> str:
    return f"{'•' * max(len(phone) - 4, 3)}{phone[-4:]}"


def _destinations_for(method: TwoFactorMethod, user: User) -> list[str]:
    destinations = []
    if method in (TwoFactorMethod.EMAIL, TwoFactorMethod.BOTH):
        destinations.append(_mask_email(user.email))
    if method in (TwoFactorMethod.PHONE, TwoFactorMethod.BOTH) and user.phone:
        destinations.append(_mask_phone(user.phone))
    return destinations


class AuthService:
    def __init__(self, repository: UserRepository, security_settings: SecuritySettings) -> None:
        self._repository = repository
        self._settings = security_settings

    async def register(self, email: str, password: str) -> User:
        existing = await self._repository.get_by_email(email)
        if existing is not None:
            raise ConflictError("An account with this email already exists.")

        user = User(email=email, hashed_password=hash_password(password), role=UserRole.CANDIDATE)
        return await self._repository.create(user)

    async def login(
        self, email: str, password: str, *, email_sender: EmailSender, sms_sender: SmsSender
    ) -> TokenResponse | TwoFactorChallengeResponse:
        user = await self._repository.get_by_email(email)
        if user is None or not verify_password(password, user.hashed_password):
            raise UnauthorizedError("Invalid email or password.")
        if not user.is_active:
            raise UnauthorizedError("This account is disabled.")

        if user.two_factor_enabled and user.two_factor_method is not None:
            method = user.two_factor_method
            await self._issue_otp(
                user, OtpPurpose.TWO_FACTOR_LOGIN, method, email_sender, sms_sender
            )
            challenge_token = create_two_factor_challenge_token(
                user_id=user.id, settings=self._settings
            )
            return TwoFactorChallengeResponse(
                challenge_token=challenge_token,
                method=method,
                masked_destinations=_destinations_for(method, user),
            )

        return await self._issue_tokens(user)

    async def refresh(self, raw_refresh_token: str) -> TokenResponse:
        token_hash = hash_opaque_token(raw_refresh_token)
        stored_token = await self._repository.get_refresh_token_by_hash(token_hash)

        if stored_token is None or stored_token.revoked_at is not None:
            raise UnauthorizedError("Refresh token is invalid or has been revoked.")
        if stored_token.expires_at < datetime.now(UTC):
            raise UnauthorizedError("Refresh token has expired.")

        await self._repository.revoke_refresh_token(stored_token)

        user = await self._repository.get_by_id(stored_token.user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("Account is no longer active.")

        return await self._issue_tokens(user)

    async def logout(self, raw_refresh_token: str) -> None:
        token_hash = hash_opaque_token(raw_refresh_token)
        stored_token = await self._repository.get_refresh_token_by_hash(token_hash)
        if stored_token is not None and stored_token.revoked_at is None:
            await self._repository.revoke_refresh_token(stored_token)

    async def forgot_password(
        self, email: str, *, email_sender: EmailSender, frontend_base_url: str
    ) -> None:
        """Always returns None regardless of whether the email exists — the caller (route) must
        not branch on this to avoid leaking which emails are registered."""
        user = await self._repository.get_by_email(email)
        if user is None:
            return

        raw_token = generate_opaque_token()
        token = PasswordResetToken(
            user_id=user.id,
            token_hash=hash_opaque_token(raw_token),
            expires_at=datetime.now(UTC)
            + timedelta(minutes=self._settings.password_reset_token_ttl_minutes),
        )
        await self._repository.add_password_reset_token(token)

        reset_link = f"{frontend_base_url}/reset-password?token={raw_token}"
        await email_sender.send(
            to=user.email,
            subject="Reset your Chatfolio password",
            body=(
                "We received a request to reset your Chatfolio password.\n\n"
                f"Reset it here: {reset_link}\n\n"
                f"This link expires in {self._settings.password_reset_token_ttl_minutes} minutes. "
                "If you didn't request this, you can safely ignore this email."
            ),
        )

    async def reset_password(self, raw_token: str, new_password: str) -> None:
        token_hash = hash_opaque_token(raw_token)
        stored = await self._repository.get_password_reset_token_by_hash(token_hash)

        if (
            stored is None
            or stored.used_at is not None
            or stored.expires_at < datetime.now(UTC)
        ):
            raise UnauthorizedError("This reset link is invalid or has expired.")

        user = await self._repository.get_by_id(stored.user_id)
        if user is None:
            raise UnauthorizedError("This reset link is invalid or has expired.")

        user.hashed_password = hash_password(new_password)
        stored.used_at = datetime.now(UTC)
        await self._repository.revoke_all_refresh_tokens_for_user(user.id)

    async def change_password(
        self, user: User, current_password: str, new_password: str
    ) -> None:
        if not verify_password(current_password, user.hashed_password):
            raise UnauthorizedError("Current password is incorrect.")
        # Deliberately does NOT revoke other sessions — unlike forgot/reset-password (a
        # compromise-recovery flow), this is an already-logged-in user who just proved they know
        # the current password, not evidence the account was taken over elsewhere.
        user.hashed_password = hash_password(new_password)

    async def request_email_change(
        self,
        user: User,
        new_email: str,
        password: str,
        *,
        email_sender: EmailSender,
        frontend_base_url: str,
    ) -> None:
        if not verify_password(password, user.hashed_password):
            raise UnauthorizedError("Password is incorrect.")

        existing = await self._repository.get_by_email(new_email)
        if existing is not None:
            raise ConflictError("An account with this email already exists.")

        raw_token = generate_opaque_token()
        request = EmailChangeRequest(
            user_id=user.id,
            new_email=new_email,
            token_hash=hash_opaque_token(raw_token),
            expires_at=datetime.now(UTC)
            + timedelta(minutes=self._settings.email_change_token_ttl_minutes),
        )
        await self._repository.add_email_change_request(request)

        confirm_link = f"{frontend_base_url}/confirm-email-change?token={raw_token}"
        await email_sender.send(
            to=new_email,
            subject="Confirm your new Chatfolio email address",
            body=(
                "We received a request to change the email address on your Chatfolio account "
                "to this one.\n\n"
                f"Confirm it here: {confirm_link}\n\n"
                f"This link expires in {self._settings.email_change_token_ttl_minutes} minutes. "
                "If you didn't request this, you can safely ignore this email — your account "
                "email will not change."
            ),
        )

    async def confirm_email_change(self, raw_token: str) -> User:
        token_hash = hash_opaque_token(raw_token)
        stored = await self._repository.get_email_change_request_by_hash(token_hash)

        if (
            stored is None
            or stored.used_at is not None
            or stored.expires_at < datetime.now(UTC)
        ):
            raise UnauthorizedError("This confirmation link is invalid or has expired.")

        # Re-check uniqueness at confirm time too, not just at request time — the address could
        # have been claimed by a different account in the time between the two.
        existing = await self._repository.get_by_email(stored.new_email)
        if existing is not None:
            raise ConflictError("An account with this email already exists.")

        user = await self._repository.get_by_id(stored.user_id)
        if user is None:
            raise UnauthorizedError("This confirmation link is invalid or has expired.")

        user.email = stored.new_email
        stored.used_at = datetime.now(UTC)
        return user

    async def setup_two_factor(
        self,
        user: User,
        method: TwoFactorMethod,
        phone: str | None,
        *,
        email_sender: EmailSender,
        sms_sender: SmsSender,
    ) -> TwoFactorSetupResponse:
        needs_phone = method in (TwoFactorMethod.PHONE, TwoFactorMethod.BOTH)
        if needs_phone and phone:
            user.phone = phone
        if needs_phone and not user.phone:
            raise ValidationFailedError("A phone number is required for this two-factor method.")

        await self._issue_otp(user, OtpPurpose.TWO_FACTOR_ENROLL, method, email_sender, sms_sender)
        return TwoFactorSetupResponse(
            method=method, masked_destinations=_destinations_for(method, user)
        )

    async def verify_two_factor_setup(self, user: User, code: str) -> None:
        otp = await self._consume_otp(user.id, OtpPurpose.TWO_FACTOR_ENROLL, code)
        user.two_factor_enabled = True
        user.two_factor_method = otp.channel

    async def verify_two_factor_login(
        self, challenge_token: str, code: str
    ) -> TokenResponse:
        user_id = self._decode_challenge(challenge_token)
        await self._consume_otp(user_id, OtpPurpose.TWO_FACTOR_LOGIN, code)

        user = await self._repository.get_by_id(user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("Account is no longer active.")
        return await self._issue_tokens(user)

    async def resend_two_factor_login_code(
        self, challenge_token: str, *, email_sender: EmailSender, sms_sender: SmsSender
    ) -> None:
        user_id = self._decode_challenge(challenge_token)
        user = await self._repository.get_by_id(user_id)
        if user is None or not user.two_factor_enabled or user.two_factor_method is None:
            raise UnauthorizedError("This login challenge is no longer valid.")

        previous = await self._repository.get_latest_active_otp(
            user_id, OtpPurpose.TWO_FACTOR_LOGIN
        )
        if previous is not None:
            previous.consumed_at = datetime.now(UTC)

        await self._issue_otp(
            user, OtpPurpose.TWO_FACTOR_LOGIN, user.two_factor_method, email_sender, sms_sender
        )

    def _decode_challenge(self, challenge_token: str) -> uuid.UUID:
        try:
            return decode_two_factor_challenge_token(challenge_token, self._settings)
        except jwt.ExpiredSignatureError as exc:
            raise UnauthorizedError(
                "This login challenge has expired. Please log in again."
            ) from exc
        except jwt.InvalidTokenError as exc:
            raise UnauthorizedError("This login challenge is invalid.") from exc

    async def _issue_otp(
        self,
        user: User,
        purpose: OtpPurpose,
        method: TwoFactorMethod,
        email_sender: EmailSender,
        sms_sender: SmsSender,
    ) -> None:
        code = generate_otp_code()
        otp = OtpCode(
            user_id=user.id,
            purpose=purpose,
            channel=method,
            code_hash=hash_opaque_token(code),
            expires_at=datetime.now(UTC) + timedelta(minutes=self._settings.otp_ttl_minutes),
        )
        await self._repository.add_otp_code(otp)

        message = (
            f"Your Chatfolio verification code is {code}. "
            f"It expires in {self._settings.otp_ttl_minutes} minutes."
        )
        if method in (TwoFactorMethod.EMAIL, TwoFactorMethod.BOTH):
            await email_sender.send(
                to=user.email, subject="Your Chatfolio verification code", body=message
            )
        if method in (TwoFactorMethod.PHONE, TwoFactorMethod.BOTH) and user.phone:
            await sms_sender.send(to=user.phone, message=message)

    async def _consume_otp(self, user_id: uuid.UUID, purpose: OtpPurpose, code: str) -> OtpCode:
        otp = await self._repository.get_latest_active_otp(user_id, purpose)
        if otp is None or otp.expires_at < datetime.now(UTC):
            raise UnauthorizedError("This code is invalid or has expired.")
        if otp.attempts >= self._settings.otp_max_attempts:
            raise UnauthorizedError("Too many incorrect attempts. Request a new code.")

        if hash_opaque_token(code) != otp.code_hash:
            otp.attempts += 1
            raise UnauthorizedError("Incorrect verification code.")

        otp.consumed_at = datetime.now(UTC)
        return otp

    async def _issue_tokens(self, user: User) -> TokenResponse:
        access_token = create_access_token(user_id=user.id, role=user.role, settings=self._settings)

        raw_refresh_token = generate_opaque_token()
        refresh_token = RefreshToken(
            user_id=user.id,
            token_hash=hash_opaque_token(raw_refresh_token),
            expires_at=datetime.now(UTC) + timedelta(days=self._settings.refresh_token_ttl_days),
        )
        await self._repository.add_refresh_token(refresh_token)

        return TokenResponse(access_token=access_token, refresh_token=raw_refresh_token)
