import uuid
from collections.abc import Callable
from typing import Annotated, Any

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.billing.strategy import ActivationStrategy, StrategyName
from chatfolio.billing.support_kori import SupportKoriLicenseStrategy
from chatfolio.config.settings import Settings, get_settings
from chatfolio.core.exceptions import ForbiddenError, UnauthorizedError
from chatfolio.core.redis import get_redis
from chatfolio.core.security import decode_access_token
from chatfolio.db.session import get_db_session
from chatfolio.entitlements.cache import EntitlementCache
from chatfolio.entitlements.catalog import FeatureKey
from chatfolio.llm.base import LLMFactory
from chatfolio.llm.factory import LLMProviderFactory
from chatfolio.llm.model_router import ModelRouter
from chatfolio.models.user import User, UserRole
from chatfolio.notifications.base import EmailSender, SmsSender
from chatfolio.notifications.sms_vendor import VendorSmsSender
from chatfolio.notifications.smtp_email import SmtpEmailSender
from chatfolio.repositories.profile_repository import ProfileRepository
from chatfolio.repositories.user_repository import UserRepository
from chatfolio.services.billing_service import BillingService
from chatfolio.services.billing_settings_service import BillingSettingsService
from chatfolio.services.entitlement_guard import EntitlementGuard
from chatfolio.services.entitlement_service import EntitlementService
from chatfolio.services.portfolio_service import PortfolioService, PublicPortfolioService
from chatfolio.services.pricing_service import PricingService
from chatfolio.services.profile_service import ProfileService
from chatfolio.services.quota_service import QuotaService
from chatfolio.services.sms_service import SmsService
from chatfolio.storage.base import StorageBackend
from chatfolio.storage.s3_storage import S3StorageBackend
from chatfolio.vectorstore.base import VectorStore
from chatfolio.vectorstore.chroma_store import ChromaVectorStore
from chatfolio.vectorstore.local_embedder import embed_texts
from chatfolio.workers.queue import JobQueue, get_arq_pool

_bearer_scheme = HTTPBearer(auto_error=False)

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


def get_entitlement_cache() -> EntitlementCache:
    return EntitlementCache(get_redis())


EntitlementCacheDep = Annotated[EntitlementCache, Depends(get_entitlement_cache)]


def get_entitlement_service(
    session: DbSessionDep, cache: EntitlementCacheDep
) -> EntitlementService:
    return EntitlementService(session, cache)


EntitlementServiceDep = Annotated[EntitlementService, Depends(get_entitlement_service)]


def get_quota_service(session: DbSessionDep) -> QuotaService:
    return QuotaService(session, get_redis())


QuotaServiceDep = Annotated[QuotaService, Depends(get_quota_service)]


def get_entitlement_guard(
    entitlements: EntitlementServiceDep, quota: QuotaServiceDep
) -> EntitlementGuard:
    return EntitlementGuard(entitlements, quota)


GuardDep = Annotated[EntitlementGuard, Depends(get_entitlement_guard)]


def get_portfolio_service(session: DbSessionDep, guard: GuardDep) -> PortfolioService:
    return PortfolioService(session, ProfileService(ProfileRepository(session)), guard)


PortfolioServiceDep = Annotated[PortfolioService, Depends(get_portfolio_service)]


def get_public_portfolio_service(
    session: DbSessionDep, guard: GuardDep
) -> PublicPortfolioService:
    return PublicPortfolioService(session, ProfileRepository(session), guard)


PublicPortfolioServiceDep = Annotated[
    PublicPortfolioService, Depends(get_public_portfolio_service)
]


def get_activation_strategies(settings: SettingsDep) -> dict[StrategyName, ActivationStrategy]:
    """Which strategies exist in this deployment. Tests override this to fake the seller."""
    return {StrategyName.SUPPORT_KORI_LICENSE: SupportKoriLicenseStrategy(settings.support_kori)}


ActivationStrategiesDep = Annotated[
    dict[StrategyName, ActivationStrategy], Depends(get_activation_strategies)
]


def get_billing_service(
    session: DbSessionDep,
    cache: EntitlementCacheDep,
    settings: SettingsDep,
    strategies: ActivationStrategiesDep,
) -> BillingService:
    return BillingService(
        session, cache, get_redis(), settings.security, settings.support_kori, strategies
    )


BillingServiceDep = Annotated[BillingService, Depends(get_billing_service)]


def get_billing_settings_service(
    session: DbSessionDep, cache: EntitlementCacheDep
) -> BillingSettingsService:
    return BillingSettingsService(session, cache)


BillingSettingsServiceDep = Annotated[
    BillingSettingsService, Depends(get_billing_settings_service)
]


def get_pricing_service(session: DbSessionDep, cache: EntitlementCacheDep) -> PricingService:
    return PricingService(session, cache)


PricingServiceDep = Annotated[PricingService, Depends(get_pricing_service)]


def get_user_repository(session: DbSessionDep) -> UserRepository:
    return UserRepository(session)


UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]


def get_email_sender(settings: SettingsDep) -> EmailSender:
    return SmtpEmailSender(settings.email)


EmailSenderDep = Annotated[EmailSender, Depends(get_email_sender)]


def get_sms_sender(settings: SettingsDep) -> SmsSender:
    return VendorSmsSender(settings.sms)


SmsSenderDep = Annotated[SmsSender, Depends(get_sms_sender)]


def get_sms_service(sms_sender: SmsSenderDep) -> SmsService:
    return SmsService(sms_sender)


SmsServiceDep = Annotated[SmsService, Depends(get_sms_service)]


def get_storage_backend(settings: SettingsDep) -> StorageBackend:
    return S3StorageBackend(settings.storage)


StorageBackendDep = Annotated[StorageBackend, Depends(get_storage_backend)]


async def get_job_queue() -> JobQueue:
    return await get_arq_pool()


JobQueueDep = Annotated[JobQueue, Depends(get_job_queue)]


def get_model_router(
    session: DbSessionDep,
    entitlements: EntitlementServiceDep,
    cache: EntitlementCacheDep,
    settings: SettingsDep,
) -> ModelRouter:
    return ModelRouter(session, entitlements, cache, settings.llm)


ModelRouterDep = Annotated[ModelRouter, Depends(get_model_router)]


def get_llm_provider_factory(
    settings: SettingsDep, router: ModelRouterDep, quota: QuotaServiceDep
) -> LLMFactory:
    return LLMProviderFactory(settings.llm, router, quota)


LLMFactoryDep = Annotated[LLMFactory, Depends(get_llm_provider_factory)]


_vector_store: VectorStore | None = None


def get_vector_store(settings: SettingsDep) -> VectorStore:
    # Process-lifetime singleton, not one-per-request: a fresh instance means a fresh
    # chromadb.AsyncHttpClient every single request, which was observed to intermittently fail
    # against the live Chroma server under back-to-back requests (a bare script reusing one
    # client never reproduced it). Matches the same lazy-singleton pattern already used for the
    # DB engine (db/session.py) and the arq pool (workers/queue.py).
    global _vector_store
    if _vector_store is None:
        _vector_store = ChromaVectorStore(settings.vectorstore)
    return _vector_store


VectorStoreDep = Annotated[VectorStore, Depends(get_vector_store)]


def get_embed_texts() -> Callable[[list[str]], list[list[float]]]:
    return embed_texts


EmbedTextsDep = Annotated[Callable[[list[str]], list[list[float]]], Depends(get_embed_texts)]


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


def require_feature(feature: FeatureKey) -> Any:
    """Route-level gate for on/off features: `dependencies=[require_feature(FeatureKey.X)]`.
    Readable at the route, and the `__feature__` marker lets a test prove every gated route
    carries one. Quotas are *not* enforced here — those need atomic consume/release around the
    action itself, so they live in the services."""

    async def _require(current_user: CurrentUserDep, guard: GuardDep) -> None:
        await guard.require_feature(current_user.id, feature)

    _require.__feature__ = feature  # type: ignore[attr-defined]
    return Depends(_require)
