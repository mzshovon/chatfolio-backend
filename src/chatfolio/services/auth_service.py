from datetime import UTC, datetime, timedelta

from chatfolio.config.settings import SecuritySettings
from chatfolio.core.exceptions import ConflictError, UnauthorizedError
from chatfolio.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from chatfolio.models.user import RefreshToken, User, UserRole
from chatfolio.repositories.user_repository import UserRepository
from chatfolio.schemas.auth import TokenResponse


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

    async def login(self, email: str, password: str) -> TokenResponse:
        user = await self._repository.get_by_email(email)
        if user is None or not verify_password(password, user.hashed_password):
            raise UnauthorizedError("Invalid email or password.")
        if not user.is_active:
            raise UnauthorizedError("This account is disabled.")

        return await self._issue_tokens(user)

    async def refresh(self, raw_refresh_token: str) -> TokenResponse:
        token_hash = hash_refresh_token(raw_refresh_token)
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
        token_hash = hash_refresh_token(raw_refresh_token)
        stored_token = await self._repository.get_refresh_token_by_hash(token_hash)
        if stored_token is not None and stored_token.revoked_at is None:
            await self._repository.revoke_refresh_token(stored_token)

    async def _issue_tokens(self, user: User) -> TokenResponse:
        access_token = create_access_token(user_id=user.id, role=user.role, settings=self._settings)

        raw_refresh_token = generate_refresh_token()
        refresh_token = RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(raw_refresh_token),
            expires_at=datetime.now(UTC) + timedelta(days=self._settings.refresh_token_ttl_days),
        )
        await self._repository.add_refresh_token(refresh_token)

        return TokenResponse(access_token=access_token, refresh_token=raw_refresh_token)
