import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.config.settings import FeatureFlags
from chatfolio.core.exceptions import ConflictError, NotFoundError
from chatfolio.entitlements.catalog import FeatureKey
from chatfolio.models.domain import PublicDomain
from chatfolio.models.user import User
from chatfolio.services.entitlement_guard import EntitlementGuard
from chatfolio.services.portfolio_service import PortfolioService


class DomainService:
    """Phase-2 stub (§15) behind `features.enable_custom_domains` — every method 404s while the
    flag is off, same as any other not-yet-shipped surface, rather than exposing a half-working
    feature. No DNS verification is implemented; `verification_token` is generated and stored
    so a future verifier has something to challenge against.

    A chatfolio may hold up to the plan's `custom_domains_max` domains. The original single-domain
    methods (`get_domain`, `replace_domain`, `remove_domain`) keep working on the oldest domain.
    """

    def __init__(
        self,
        session: AsyncSession,
        portfolio_service: PortfolioService,
        features: FeatureFlags,
        guard: EntitlementGuard,
    ) -> None:
        self._session = session
        self._portfolio_service = portfolio_service
        self._features = features
        self._guard = guard

    async def list_domains(self, user: User) -> list[PublicDomain]:
        self._require_enabled()
        chatfolio = await self._portfolio_service.get_or_create_for_user(user)
        return await self._domains(chatfolio.id)

    async def get_domain(self, user: User) -> PublicDomain | None:
        domains = await self.list_domains(user)
        return domains[0] if domains else None

    async def add_domain(self, user: User, domain: str) -> PublicDomain:
        self._require_enabled()
        chatfolio = await self._portfolio_service.get_or_create_for_user(user)
        await self._guard.lock_stock(user.id, FeatureKey.CUSTOM_DOMAINS_MAX)
        await self._ensure_free(domain)
        current = await self._domains(chatfolio.id)
        await self._guard.lock_and_check_room(user.id, FeatureKey.CUSTOM_DOMAINS_MAX, len(current))
        return await self._create(chatfolio.id, domain)

    async def replace_domain(self, user: User, domain: str) -> PublicDomain:
        """Single-domain behaviour: swap the oldest domain for a new one (or add the first)."""
        self._require_enabled()
        chatfolio = await self._portfolio_service.get_or_create_for_user(user)
        await self._guard.lock_stock(user.id, FeatureKey.CUSTOM_DOMAINS_MAX)
        await self._ensure_free(domain)
        current = await self._domains(chatfolio.id)
        if current:
            await self._session.delete(current[0])
            await self._session.flush()
            current = current[1:]
        await self._guard.lock_and_check_room(user.id, FeatureKey.CUSTOM_DOMAINS_MAX, len(current))
        return await self._create(chatfolio.id, domain)

    async def remove_domain(self, user: User, domain_id: uuid.UUID | None = None) -> None:
        self._require_enabled()
        chatfolio = await self._portfolio_service.get_or_create_for_user(user)
        domains = await self._domains(chatfolio.id)
        if domain_id is None:
            target = domains[0] if domains else None
        else:
            target = next((d for d in domains if d.id == domain_id), None)
        if target is None:
            raise NotFoundError("No custom domain is configured.")
        await self._session.delete(target)
        await self._session.flush()

    async def _domains(self, chatfolio_id: uuid.UUID) -> list[PublicDomain]:
        result = await self._session.execute(
            select(PublicDomain)
            .where(PublicDomain.chatfolio_id == chatfolio_id)
            .order_by(PublicDomain.created_at, PublicDomain.id)
        )
        return list(result.scalars())

    async def _ensure_free(self, domain: str) -> None:
        existing = await self._session.execute(
            select(PublicDomain).where(PublicDomain.domain == domain)
        )
        if existing.scalar_one_or_none() is not None:
            raise ConflictError(f"Domain {domain!r} is already in use.")

    async def _create(self, chatfolio_id: uuid.UUID, domain: str) -> PublicDomain:
        record = PublicDomain(
            chatfolio_id=chatfolio_id,
            domain=domain,
            verification_token=secrets.token_hex(16),
        )
        self._session.add(record)
        await self._session.flush()
        return record

    def _require_enabled(self) -> None:
        if not self._features.enable_custom_domains:
            raise NotFoundError("Custom domains are not available yet.")
