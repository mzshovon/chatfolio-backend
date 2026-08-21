import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.config.settings import FeatureFlags
from chatfolio.core.exceptions import ConflictError, NotFoundError
from chatfolio.models.domain import PublicDomain
from chatfolio.models.user import User
from chatfolio.services.portfolio_service import PortfolioService


class DomainService:
    """Phase-2 stub (§15) behind `features.enable_custom_domains` — every method 404s while the
    flag is off, same as any other not-yet-shipped surface, rather than exposing a half-working
    feature. No DNS verification is implemented; `verification_token` is generated and stored
    so a future verifier has something to challenge against.
    """

    def __init__(
        self, session: AsyncSession, portfolio_service: PortfolioService, features: FeatureFlags
    ) -> None:
        self._session = session
        self._portfolio_service = portfolio_service
        self._features = features

    async def get_domain(self, user: User) -> PublicDomain | None:
        self._require_enabled()
        chatfolio = await self._portfolio_service.get_or_create_for_user(user)
        return await self._get_by_chatfolio_id(chatfolio.id)

    async def add_domain(self, user: User, domain: str) -> PublicDomain:
        self._require_enabled()
        chatfolio = await self._portfolio_service.get_or_create_for_user(user)

        existing_result = await self._session.execute(
            select(PublicDomain).where(PublicDomain.domain == domain)
        )
        if existing_result.scalar_one_or_none() is not None:
            raise ConflictError(f"Domain {domain!r} is already in use.")

        current = await self._get_by_chatfolio_id(chatfolio.id)
        if current is not None:
            await self._session.delete(current)
            await self._session.flush()

        record = PublicDomain(
            chatfolio_id=chatfolio.id,
            domain=domain,
            verification_token=secrets.token_hex(16),
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def remove_domain(self, user: User) -> None:
        self._require_enabled()
        chatfolio = await self._portfolio_service.get_or_create_for_user(user)
        record = await self._get_by_chatfolio_id(chatfolio.id)
        if record is None:
            raise NotFoundError("No custom domain is configured.")
        await self._session.delete(record)
        await self._session.flush()

    async def _get_by_chatfolio_id(self, chatfolio_id: uuid.UUID) -> PublicDomain | None:
        result = await self._session.execute(
            select(PublicDomain).where(PublicDomain.chatfolio_id == chatfolio_id)
        )
        return result.scalar_one_or_none()

    def _require_enabled(self) -> None:
        if not self._features.enable_custom_domains:
            raise NotFoundError("Custom domains are not available yet.")
