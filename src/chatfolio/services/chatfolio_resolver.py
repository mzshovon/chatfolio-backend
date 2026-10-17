from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.entitlements.catalog import FeatureKey
from chatfolio.models.chatfolio import ChatfolioLink, PublicChatfolio
from chatfolio.models.profile import CandidateProfile
from chatfolio.services.entitlement_guard import EntitlementGuard


class ChatfolioResolver:
    """Public slug -> published Chatfolio, following aliases.

    A slug resolves if it is the chatfolio's primary slug, or an *active* alias that is still
    inside the owner's plan: aliases are honoured oldest-first up to `links_max - 1` (the primary
    takes one slot), so after a downgrade or expiry the extras simply stop resolving — nothing is
    deleted, and they come back on re-upgrade."""

    def __init__(self, session: AsyncSession, guard: EntitlementGuard) -> None:
        self._session = session
        self._guard = guard

    async def resolve_published(self, slug: str) -> PublicChatfolio | None:
        primary = (
            await self._session.execute(
                select(PublicChatfolio).where(
                    PublicChatfolio.slug == slug, PublicChatfolio.is_published.is_(True)
                )
            )
        ).scalar_one_or_none()
        if primary is not None:
            return primary

        link = (
            await self._session.execute(
                select(ChatfolioLink).where(
                    ChatfolioLink.slug == slug, ChatfolioLink.is_active.is_(True)
                )
            )
        ).scalar_one_or_none()
        if link is None:
            return None
        chatfolio = await self._session.get(PublicChatfolio, link.chatfolio_id)
        if chatfolio is None or not chatfolio.is_published:
            return None
        profile = await self._session.get(CandidateProfile, chatfolio.profile_id)
        if profile is None:
            return None

        entitlements = await self._guard.get(profile.user_id)
        if entitlements.is_unlimited(FeatureKey.LINKS_MAX):
            return chatfolio
        allowed = max(entitlements.limit(FeatureKey.LINKS_MAX) - 1, 0)
        active_ids = (
            await self._session.execute(
                select(ChatfolioLink.id)
                .where(
                    ChatfolioLink.chatfolio_id == chatfolio.id, ChatfolioLink.is_active.is_(True)
                )
                .order_by(ChatfolioLink.created_at, ChatfolioLink.id)
                .limit(allowed)
            )
        ).scalars()
        return chatfolio if link.id in set(active_ids) else None
