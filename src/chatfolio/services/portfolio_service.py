import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import TypeVar

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.core.exceptions import ConflictError, ValidationFailedError
from chatfolio.models.chat import ChatSession, RecruiterMetadata
from chatfolio.models.chatfolio import PortfolioVisit, PublicChatfolio
from chatfolio.models.cv import CVStatus, UploadedCV
from chatfolio.models.mixins import ProfileChildMixin
from chatfolio.models.portfolio_section import PortfolioSection, SectionStatus, SectionType
from chatfolio.models.profile import CandidateProfile, ProfileStatus
from chatfolio.models.user import User
from chatfolio.repositories.profile_repository import ProfileRepository
from chatfolio.schemas.portfolio_settings import PortfolioSettingsUpdateRequest
from chatfolio.services.profile_service import ProfileService

SUBDOMAIN_SUFFIX = ".chatfolio.com"

ChildT = TypeVar("ChildT", bound=ProfileChildMixin)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "candidate"


def subdomain_for(slug: str) -> str:
    return f"{slug}{SUBDOMAIN_SUFFIX}"


class PortfolioService:
    """Owner-facing: portfolio settings, slug management, publish/unpublish gating."""

    def __init__(self, session: AsyncSession, profile_service: ProfileService) -> None:
        self._session = session
        self._profile_service = profile_service

    async def get_or_create_for_user(self, user: User) -> PublicChatfolio:
        profile = await self._profile_service.get_or_create_for_user(user)
        result = await self._session.execute(
            select(PublicChatfolio).where(PublicChatfolio.profile_id == profile.id)
        )
        chatfolio = result.scalar_one_or_none()
        if chatfolio is None:
            slug = await self._generate_unique_slug(profile.full_name or "candidate")
            chatfolio = PublicChatfolio(profile_id=profile.id, slug=slug)
            self._session.add(chatfolio)
            await self._session.flush()
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

        profile.status = ProfileStatus.APPROVED
        chatfolio = await self.get_or_create_for_user(user)
        chatfolio.is_published = True
        chatfolio.published_at = datetime.now(UTC)
        await self._session.flush()
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
        return result.scalar_one_or_none() is not None

    async def _ensure_slug_available(self, slug: str, *, exclude_id: uuid.UUID) -> None:
        result = await self._session.execute(
            select(PublicChatfolio.id).where(
                PublicChatfolio.slug == slug, PublicChatfolio.id != exclude_id
            )
        )
        if result.scalar_one_or_none() is not None:
            raise ConflictError(f"Slug {slug!r} is already taken.")


class PublicPortfolioService:
    """Public, unauthenticated: resolves a Chatfolio by slug. Never reads draft/unapproved data —
    only `is_published` chatfolios, and only `approved` PortfolioSection content, are exposed."""

    def __init__(self, session: AsyncSession, profile_repository: ProfileRepository) -> None:
        self._session = session
        self._profile_repository = profile_repository

    async def get_published_by_slug(self, slug: str) -> PublicChatfolio | None:
        result = await self._session.execute(
            select(PublicChatfolio).where(
                PublicChatfolio.slug == slug, PublicChatfolio.is_published.is_(True)
            )
        )
        return result.scalar_one_or_none()

    async def find_redirect_target(self, old_slug: str) -> str | None:
        result = await self._session.execute(
            select(PublicChatfolio.slug).where(
                PublicChatfolio.previous_slug == old_slug, PublicChatfolio.is_published.is_(True)
            )
        )
        return result.scalar_one_or_none()

    async def get_profile(self, profile_id: uuid.UUID) -> CandidateProfile | None:
        return await self._session.get(CandidateProfile, profile_id)

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
