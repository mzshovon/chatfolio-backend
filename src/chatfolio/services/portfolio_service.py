import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import TypeVar

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.core.exceptions import ConflictError, NotFoundError, ValidationFailedError
from chatfolio.entitlements.catalog import FeatureKey
from chatfolio.models.chat import ChatSession, RecruiterMetadata
from chatfolio.models.chatfolio import ChatfolioLink, PortfolioVisit, PublicChatfolio
from chatfolio.models.cv import CVStatus, UploadedCV
from chatfolio.models.mixins import ProfileChildMixin
from chatfolio.models.portfolio_section import PortfolioSection, SectionStatus, SectionType
from chatfolio.models.profile import (
    CandidateProfile,
    Experience,
    JobType,
    ProfileStatus,
)
from chatfolio.models.user import User
from chatfolio.repositories.profile_repository import ProfileRepository
from chatfolio.schemas.portfolio_settings import PortfolioSettingsUpdateRequest
from chatfolio.services.chatfolio_resolver import ChatfolioResolver
from chatfolio.services.entitlement_guard import EntitlementGuard
from chatfolio.services.profile_service import ProfileService

SUBDOMAIN_SUFFIX = ".chatfolio.chat"

ChildT = TypeVar("ChildT", bound=ProfileChildMixin)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "candidate"


def subdomain_for(slug: str) -> str:
    return f"{slug}{SUBDOMAIN_SUFFIX}"


class PortfolioService:
    """Owner-facing: portfolio settings, slug management, publish/unpublish gating."""

    def __init__(
        self, session: AsyncSession, profile_service: ProfileService, guard: EntitlementGuard
    ) -> None:
        self._session = session
        self._profile_service = profile_service
        self._guard = guard

    async def get_or_create_for_user(self, user: User) -> PublicChatfolio:
        profile = await self._profile_service.get_or_create_for_user(user)
        result = await self._session.execute(
            select(PublicChatfolio).where(PublicChatfolio.profile_id == profile.id)
        )
        chatfolio = result.scalar_one_or_none()
        if chatfolio is None:
            profile_id = profile.id
            slug = await self._generate_unique_slug(profile.full_name or "candidate")
            try:
                # A SAVEPOINT confines the rollback to this insert on conflict, instead of
                # expiring every object already loaded in the outer session (e.g. `profile`).
                async with self._session.begin_nested():
                    chatfolio = PublicChatfolio(profile_id=profile_id, slug=slug)
                    self._session.add(chatfolio)
                    await self._session.flush()
            except IntegrityError:
                # Lost a race against a concurrent request creating the same chatfolio
                # (e.g. parallel dashboard calls right after registration).
                result = await self._session.execute(
                    select(PublicChatfolio).where(PublicChatfolio.profile_id == profile_id)
                )
                chatfolio = result.scalar_one_or_none()
                if chatfolio is None:
                    raise
        return chatfolio

    async def update_settings(
        self, user: User, payload: PortfolioSettingsUpdateRequest
    ) -> PublicChatfolio:
        chatfolio = await self.get_or_create_for_user(user)
        data = payload.model_dump(exclude_unset=True)

        new_slug = data.pop("slug", None)
        if new_slug is not None and new_slug != chatfolio.slug:
            await self._ensure_slug_available(new_slug, exclude_id=chatfolio.id)
            chatfolio.previous_slug = chatfolio.slug
            chatfolio.slug = new_slug

        for field, value in data.items():
            setattr(chatfolio, field, value)

        await self._session.flush()
        return chatfolio

    async def publish(self, user: User) -> PublicChatfolio:
        profile = await self._profile_service.get_or_create_for_user(user)
        sections = await self._profile_service.list_children(user, PortfolioSection)
        approved_types = {s.section_type for s in sections if s.status == SectionStatus.APPROVED}

        errors: list[str] = []
        missing = [t.value for t in SectionType if t not in approved_types]
        if missing:
            errors.append(f"Approve these sections before publishing: {', '.join(missing)}.")
        if not profile.full_name:
            errors.append("Add your full name before publishing.")
        if errors:
            raise ValidationFailedError(" ".join(errors))

        chatfolio = await self.get_or_create_for_user(user)
        # Each unpublished -> published transition is one publish action against the plan's
        # `publishes_limit` (re-publishing a live page changes nothing, so it is free; so is
        # unpublishing). Reserved last, once every validation has passed.
        newly_published = not chatfolio.is_published
        if newly_published:
            await self._guard.consume(user.id, FeatureKey.PUBLISHES_LIMIT)
        try:
            profile.status = ProfileStatus.APPROVED
            chatfolio.is_published = True
            chatfolio.published_at = datetime.now(UTC)
            await self._session.flush()
        except BaseException:
            if newly_published:
                await self._guard.release(user.id, FeatureKey.PUBLISHES_LIMIT)
            raise
        return chatfolio

    async def unpublish(self, user: User) -> PublicChatfolio:
        chatfolio = await self.get_or_create_for_user(user)
        chatfolio.is_published = False
        await self._session.flush()
        return chatfolio

    async def _generate_unique_slug(self, base: str) -> str:
        base_slug = slugify(base)
        for _ in range(5):
            candidate = f"{base_slug}-{secrets.token_hex(3)}"
            if not await self._slug_exists(candidate):
                return candidate
        raise RuntimeError("Could not generate a unique slug after several attempts.")

    async def _slug_exists(self, slug: str) -> bool:
        result = await self._session.execute(
            select(PublicChatfolio.id).where(PublicChatfolio.slug == slug)
        )
        if result.scalar_one_or_none() is not None:
            return True
        alias = await self._session.execute(
            select(ChatfolioLink.id).where(ChatfolioLink.slug == slug)
        )
        return alias.scalar_one_or_none() is not None

    async def _ensure_slug_available(self, slug: str, *, exclude_id: uuid.UUID) -> None:
        result = await self._session.execute(
            select(PublicChatfolio.id).where(
                PublicChatfolio.slug == slug, PublicChatfolio.id != exclude_id
            )
        )
        taken = (
            result.scalar_one_or_none() is not None
            or (
                await self._session.execute(
                    select(ChatfolioLink.id).where(ChatfolioLink.slug == slug)
                )
            ).scalar_one_or_none()
            is not None
        )
        if taken:
            raise ConflictError(f"Slug {slug!r} is already taken.")

    # ---- aliases (extra links of the same chatfolio) ----

    async def list_aliases(self, user: User) -> list[tuple[ChatfolioLink, bool]]:
        """Each alias with whether the owner's *current* plan still honours it."""
        chatfolio = await self.get_or_create_for_user(user)
        entitlements = await self._guard.get(user.id)
        links = await self._aliases(chatfolio.id)
        allowed = (
            len(links)
            if entitlements.is_unlimited(FeatureKey.LINKS_MAX)
            else max(entitlements.limit(FeatureKey.LINKS_MAX) - 1, 0)
        )
        honoured = {link.id for link in [lk for lk in links if lk.is_active][:allowed]}
        return [(link, link.id in honoured) for link in links]

    async def add_alias(self, user: User, slug: str) -> ChatfolioLink:
        chatfolio = await self.get_or_create_for_user(user)
        await self._guard.lock_stock(user.id, FeatureKey.LINKS_MAX)
        await self._guard.lock_and_check_room(
            user.id, FeatureKey.LINKS_MAX, await self._link_count(chatfolio.id)
        )
        await self._ensure_alias_slug_available(slug)
        link = ChatfolioLink(chatfolio_id=chatfolio.id, slug=slug)
        self._session.add(link)
        await self._session.flush()
        return link

    async def update_alias(
        self, user: User, alias_id: uuid.UUID, *, slug: str | None, is_active: bool | None
    ) -> ChatfolioLink:
        chatfolio = await self.get_or_create_for_user(user)
        link = await self._owned_alias(chatfolio.id, alias_id)
        if slug is not None and slug != link.slug:
            await self._ensure_alias_slug_available(slug, exclude_alias_id=link.id)
            link.slug = slug
        if is_active is True and not link.is_active:
            await self._guard.lock_stock(user.id, FeatureKey.LINKS_MAX)
            await self._guard.lock_and_check_room(
                user.id, FeatureKey.LINKS_MAX, await self._link_count(chatfolio.id)
            )
            link.is_active = True
        elif is_active is False:
            link.is_active = False
        await self._session.flush()
        return link

    async def delete_alias(self, user: User, alias_id: uuid.UUID) -> None:
        chatfolio = await self.get_or_create_for_user(user)
        link = await self._owned_alias(chatfolio.id, alias_id)
        await self._session.delete(link)
        await self._session.flush()

    async def _aliases(self, chatfolio_id: uuid.UUID) -> list[ChatfolioLink]:
        result = await self._session.execute(
            select(ChatfolioLink)
            .where(ChatfolioLink.chatfolio_id == chatfolio_id)
            .order_by(ChatfolioLink.created_at, ChatfolioLink.id)
        )
        return list(result.scalars())

    async def _link_count(self, chatfolio_id: uuid.UUID) -> int:
        """Links held right now: the primary slug plus every active alias."""
        active = (
            await self._session.execute(
                select(func.count())
                .select_from(ChatfolioLink)
                .where(
                    ChatfolioLink.chatfolio_id == chatfolio_id, ChatfolioLink.is_active.is_(True)
                )
            )
        ).scalar_one()
        return 1 + int(active)

    async def _owned_alias(self, chatfolio_id: uuid.UUID, alias_id: uuid.UUID) -> ChatfolioLink:
        link = await self._session.get(ChatfolioLink, alias_id)
        if link is None or link.chatfolio_id != chatfolio_id:
            raise NotFoundError("Alias not found.")
        return link

    async def _ensure_alias_slug_available(
        self, slug: str, *, exclude_alias_id: uuid.UUID | None = None
    ) -> None:
        in_primary = (
            await self._session.execute(
                select(PublicChatfolio.id).where(PublicChatfolio.slug == slug)
            )
        ).scalar_one_or_none()
        in_alias = (
            await self._session.execute(
                select(ChatfolioLink.id).where(
                    ChatfolioLink.slug == slug,
                    ChatfolioLink.id != (exclude_alias_id or uuid.UUID(int=0)),
                )
            )
        ).scalar_one_or_none()
        if in_primary is not None or in_alias is not None:
            raise ConflictError(f"Slug {slug!r} is already taken.")


class PublicPortfolioService:
    """Public, unauthenticated: resolves a Chatfolio by slug. Never reads draft/unapproved data —
    only `is_published` chatfolios, and only `approved` PortfolioSection content, are exposed."""

    def __init__(
        self, session: AsyncSession, profile_repository: ProfileRepository, guard: EntitlementGuard
    ) -> None:
        self._session = session
        self._profile_repository = profile_repository
        self._guard = guard
        self._resolver = ChatfolioResolver(session, guard)

    async def get_published_by_slug(self, slug: str) -> PublicChatfolio | None:
        """Primary slug or an active alias still inside the owner's plan."""
        return await self._resolver.resolve_published(slug)

    async def show_badge(self, owner_id: uuid.UUID) -> bool:
        """The "made with Chatfolio" badge is shown unless the owner's plan removes it."""
        return not (await self._guard.get(owner_id)).enabled(FeatureKey.REMOVE_BADGE)

    async def find_redirect_target(self, old_slug: str) -> str | None:
        result = await self._session.execute(
            select(PublicChatfolio.slug).where(
                PublicChatfolio.previous_slug == old_slug, PublicChatfolio.is_published.is_(True)
            )
        )
        return result.scalar_one_or_none()

    async def get_profile(self, profile_id: uuid.UUID) -> CandidateProfile | None:
        return await self._session.get(CandidateProfile, profile_id)

    async def search_published(
        self,
        *,
        username: str | None = None,
        location: str | None = None,
        job_type: JobType | None = None,
        field: str | None = None,
        limit: int = 25,
    ) -> list[tuple[PublicChatfolio, CandidateProfile]]:
        """Recruiter-facing search across published Chatfolios. Every filter is optional and
        combines with the others (AND) — `username` matches the public slug, `field` matches the
        candidate's title, any experience role or bio (e.g. "Software Engineer"), both case-insensitive partial matches.
        Never touches unpublished/draft profiles, same guarantee as `get_published_by_slug`.
        """
        stmt = (
            select(PublicChatfolio, CandidateProfile)
            .join(CandidateProfile, CandidateProfile.id == PublicChatfolio.profile_id)
            .where(PublicChatfolio.is_published.is_(True))
        )
        if username:
            stmt = stmt.where(PublicChatfolio.slug.ilike(f"%{username}%"))
        if location:
            stmt = stmt.where(CandidateProfile.location.ilike(f"%{location}%"))
        if job_type:
            stmt = stmt.where(CandidateProfile.job_type == job_type)
        if field:
            pattern = f"%{field.strip()}%"
            # Title is often empty (e.g. profile built from a CV), so also match any
            # experience role and the bio.
            role_match = (
                select(Experience.id)
                .where(Experience.profile_id == CandidateProfile.id, Experience.role.ilike(pattern))
                .exists()
            )
            stmt = stmt.where(
                or_(
                    CandidateProfile.title.ilike(pattern),
                    CandidateProfile.bio.ilike(pattern),
                    role_match,
                )
            )

        stmt = stmt.order_by(PublicChatfolio.published_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def list_approved_sections(self, profile_id: uuid.UUID) -> dict[SectionType, str]:
        result = await self._session.execute(
            select(PortfolioSection).where(
                PortfolioSection.profile_id == profile_id,
                PortfolioSection.status == SectionStatus.APPROVED,
            )
        )
        return {section.section_type: section.content for section in result.scalars().all()}

    async def list_children(self, model: type[ChildT], profile_id: uuid.UUID) -> list[ChildT]:
        return list(await self._profile_repository.list_children(model, profile_id))

    async def get_latest_parsed_cv(self, profile_id: uuid.UUID) -> UploadedCV | None:
        result = await self._session.execute(
            select(UploadedCV)
            .where(UploadedCV.profile_id == profile_id, UploadedCV.status == CVStatus.PARSED)
            .order_by(UploadedCV.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def count_identified_recruiters(self, chatfolio_id: uuid.UUID) -> int:
        """Recruiters who volunteered at least their name or company during chat — not every
        session, most of which never mention either (RecruiterMetadata is best-effort and
        optional per §6.8, so most rows are all-null)."""
        result = await self._session.execute(
            select(func.count(RecruiterMetadata.id))
            .join(ChatSession, ChatSession.id == RecruiterMetadata.session_id)
            .where(
                ChatSession.chatfolio_id == chatfolio_id,
                or_(RecruiterMetadata.name.is_not(None), RecruiterMetadata.company.is_not(None)),
            )
        )
        return result.scalar_one()

    async def record_visit(self, chatfolio_id: uuid.UUID) -> None:
        self._session.add(PortfolioVisit(chatfolio_id=chatfolio_id))
        await self._session.flush()
