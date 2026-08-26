import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update

from chatfolio.models.user import (
    EmailChangeRequest,
    OtpCode,
    OtpPurpose,
    PasswordResetToken,
    RefreshToken,
    User,
)
from chatfolio.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def create(self, user: User) -> User:
        self.session.add(user)
        await self.session.flush()
        return user

    async def add_refresh_token(self, token: RefreshToken) -> RefreshToken:
        self.session.add(token)
        await self.session.flush()
        return token

    async def get_refresh_token_by_hash(self, token_hash: str) -> RefreshToken | None:
        result = await self.session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def revoke_refresh_token(self, token: RefreshToken) -> None:
        token.revoked_at = datetime.now(UTC)
        await self.session.flush()

    async def revoke_all_refresh_tokens_for_user(self, user_id: uuid.UUID) -> None:
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await self.session.flush()

    async def add_password_reset_token(self, token: PasswordResetToken) -> PasswordResetToken:
        self.session.add(token)
        await self.session.flush()
        return token

    async def get_password_reset_token_by_hash(
        self, token_hash: str
    ) -> PasswordResetToken | None:
        result = await self.session.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def add_email_change_request(self, request: EmailChangeRequest) -> EmailChangeRequest:
        self.session.add(request)
        await self.session.flush()
        return request

    async def get_email_change_request_by_hash(
        self, token_hash: str
    ) -> EmailChangeRequest | None:
        result = await self.session.execute(
            select(EmailChangeRequest).where(EmailChangeRequest.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def add_otp_code(self, otp: OtpCode) -> OtpCode:
        self.session.add(otp)
        await self.session.flush()
        return otp

    async def get_latest_active_otp(
        self, user_id: uuid.UUID, purpose: OtpPurpose
    ) -> OtpCode | None:
        result = await self.session.execute(
            select(OtpCode)
            .where(
                OtpCode.user_id == user_id,
                OtpCode.purpose == purpose,
                OtpCode.consumed_at.is_(None),
            )
            .order_by(OtpCode.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
