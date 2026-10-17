import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.entitlements.catalog import FEATURES, FeatureKey
from chatfolio.entitlements.types import Entitlements
from chatfolio.models.billing import FeatureKind
from chatfolio.models.chatfolio import ChatfolioLink, PublicChatfolio
from chatfolio.models.domain import PublicDomain
from chatfolio.models.profile import CandidateProfile
from chatfolio.services.entitlement_guard import EntitlementGuard


async def usage_for(
    session: AsyncSession, guard: EntitlementGuard, user_id: uuid.UUID, entitlements: Entitlements
) -> dict[str, int]:
    """Current consumption per countable feature, for the CMS usage meters. Flow features come
    from the quota counters (this window); stock features are counted live from real rows."""
    usage: dict[str, int] = {}
    for definition in FEATURES:
        if definition.kind == FeatureKind.FLOW:
            usage[definition.key.value] = await guard.used(user_id, definition.key)

    chatfolio_id = (
        await session.execute(
            select(PublicChatfolio.id)
            .join(CandidateProfile, CandidateProfile.id == PublicChatfolio.profile_id)
            .where(CandidateProfile.user_id == user_id)
        )
    ).scalar_one_or_none()
    if chatfolio_id is None:
        usage[FeatureKey.LINKS_MAX.value] = 0
        usage[FeatureKey.CUSTOM_DOMAINS_MAX.value] = 0
        return usage

    aliases = (
        await session.execute(
            select(func.count())
            .select_from(ChatfolioLink)
            .where(ChatfolioLink.chatfolio_id == chatfolio_id, ChatfolioLink.is_active.is_(True))
        )
    ).scalar_one()
    domains = (
        await session.execute(
            select(func.count())
            .select_from(PublicDomain)
            .where(PublicDomain.chatfolio_id == chatfolio_id)
        )
    ).scalar_one()
    usage[FeatureKey.LINKS_MAX.value] = 1 + int(aliases)  # the primary slug + active aliases
    usage[FeatureKey.CUSTOM_DOMAINS_MAX.value] = int(domains)
    return usage
